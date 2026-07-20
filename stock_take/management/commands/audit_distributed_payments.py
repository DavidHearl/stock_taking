"""
Management command: audit_distributed_payments
──────────────────────────────────────────────
Finds customers whose payments have been double-counted by the "distribute
payments" tool.

Distributing spreads the payments from one or more Xero invoices across several
sales by writing a ``Xero Distribution`` row per sale. If the original invoice
payments are left active, the same money is counted twice and the customer shows
a large phantom account credit on the sale page (``_build_customer_payment_pool``
pools every sale + payment for a customer).

``customer_distribute_payments`` now retires the source payments in the same
transaction, so new distributions cannot cause this. This command reports the
legacy damage and retires the originals where a human has confirmed which
invoices they were.

Usage:
	python manage.py audit_distributed_payments
	python manage.py audit_distributed_payments --customer 24106
	python manage.py audit_distributed_payments --customer 24106 \
		--retire-invoices INV-0947,INV-0965,INV-1009 --dry-run
	python manage.py audit_distributed_payments --customer 24106 \
		--retire-invoices INV-0947,INV-0965,INV-1009
"""
import logging
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from stock_take.models import AnthillPayment, AnthillSale, Customer
from stock_take.customer_views import (
	_build_customer_payment_pool,
	_recalculate_customer_financials,
)

logger = logging.getLogger(__name__)

DISTRIBUTION_TYPE = 'Xero Distribution'


class Command(BaseCommand):
	help = 'Report (and optionally fix) payments double-counted by the distribute-payments tool'

	def add_arguments(self, parser):
		parser.add_argument(
			'--customer', type=int, default=None,
			help='Limit to a single Customer pk',
		)
		parser.add_argument(
			'--retire-invoices', type=str, default=None,
			help='Comma-separated Xero invoice numbers whose payments should be marked '
			     'ignored (requires --customer)',
		)
		parser.add_argument(
			'--dry-run', action='store_true',
			help='Show what would change without writing',
		)

	def handle(self, *args, **options):
		customer_pk = options['customer']
		retire_raw = options['retire_invoices']
		dry_run = options['dry_run']

		if retire_raw and not customer_pk:
			self.stderr.write(self.style.ERROR('--retire-invoices requires --customer'))
			return

		customer_ids = sorted({
			cid for cid in AnthillPayment.objects
			.filter(payment_type=DISTRIBUTION_TYPE)
			.values_list('sale__customer_id', flat=True)
			if cid is not None
		})
		if customer_pk:
			customer_ids = [customer_pk] if customer_pk in customer_ids else []
			if not customer_ids:
				self.stdout.write(f'Customer {customer_pk} has no distributed payments.')
				return

		if not customer_ids:
			self.stdout.write(self.style.SUCCESS('No distributed payments found.'))
			return

		for cid in customer_ids:
			self._report(cid)
			if retire_raw:
				invoice_numbers = [n.strip() for n in retire_raw.split(',') if n.strip()]
				self._retire(cid, invoice_numbers, dry_run)

	# ── reporting ───────────────────────────────────────────────────────────

	def _report(self, customer_pk):
		customer = Customer.objects.filter(pk=customer_pk).first()
		anchor = AnthillSale.objects.filter(customer_id=customer_pk).first()
		if not anchor:
			return
		pool = _build_customer_payment_pool(anchor)
		if not pool:
			return

		self.stdout.write(self.style.NOTICE(
			f'\n{"=" * 70}\n'
			f'CUSTOMER {customer_pk}: {customer.name if customer else "?"}\n'
			f'{"=" * 70}'
		))
		self.stdout.write(
			f'  Pooled value:  £{pool["pool_value"]}\n'
			f'  Pooled paid:   £{pool["pool_paid"]}\n'
			f'  Net credit:    £{pool["net_credit"]}\n'
			f'  Net owed:      £{pool["net_outstanding"]}'
		)

		payments = AnthillPayment.objects.filter(
			sale__customer_id=customer_pk,
		).select_related('sale').order_by('date')

		dist_total = sum(
			(p.amount or Decimal('0')) for p in payments
			if p.payment_type == DISTRIBUTION_TYPE and not p.ignored
		)
		self.stdout.write(f'  Distributed:   £{dist_total}')

		if pool['net_credit'] > Decimal('5') and dist_total > 0:
			self.stdout.write(self.style.ERROR(
				'  *** Over-credited while distributions exist — the source payments '
				'were probably never retired.'
			))

		self.stdout.write('\n  Active non-distribution payments (candidate sources):')
		for p in payments:
			if p.ignored or p.payment_type == DISTRIBUTION_TYPE:
				continue
			date_str = p.date.strftime('%d/%m/%Y') if p.date else '?'
			self.stdout.write(
				f'    pk={p.pk:<8} {p.xero_invoice_number or "?":<12} '
				f'{date_str:<12} £{p.amount or 0:<12} {p.sale.contract_number or p.sale.anthill_activity_id}'
			)

	# ── fixing ──────────────────────────────────────────────────────────────

	def _retire(self, customer_pk, invoice_numbers, dry_run):
		targets = list(
			AnthillPayment.objects
			.filter(
				sale__customer_id=customer_pk,
				xero_invoice_number__in=invoice_numbers,
				ignored=False,
			)
			.exclude(payment_type=DISTRIBUTION_TYPE)
			.select_related('sale')
		)
		if not targets:
			self.stdout.write(self.style.WARNING(
				f'\n  No active payments matched {", ".join(invoice_numbers)} — nothing to retire.'
			))
			return

		total = sum((p.amount or Decimal('0')) for p in targets)
		self.stdout.write(self.style.WARNING(
			f'\n  Retiring {len(targets)} payment(s) totalling £{total}:'
		))
		for p in targets:
			self.stdout.write(
				f'    pk={p.pk} {p.xero_invoice_number} £{p.amount} '
				f'on {p.sale.contract_number or p.sale.anthill_activity_id}'
			)

		if dry_run:
			self.stdout.write(self.style.NOTICE('  DRY RUN — no changes written.'))
			return

		with transaction.atomic():
			AnthillPayment.objects.filter(pk__in=[p.pk for p in targets]).update(ignored=True)
			customer = Customer.objects.filter(pk=customer_pk).first()
			_recalculate_customer_financials(customer)

		anchor = AnthillSale.objects.filter(customer_id=customer_pk).first()
		pool = _build_customer_payment_pool(anchor) if anchor else None
		if pool:
			self.stdout.write(self.style.SUCCESS(
				f'  Done. Pooled paid is now £{pool["pool_paid"]} against £{pool["pool_value"]} '
				f'(credit £{pool["net_credit"]}, owed £{pool["net_outstanding"]}).'
			))
		logger.info(
			'Retired %s duplicated payment(s) totalling %s for customer %s',
			len(targets), total, customer_pk,
		)
