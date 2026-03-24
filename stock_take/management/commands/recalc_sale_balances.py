from decimal import Decimal

from django.core.management.base import BaseCommand

from stock_take.models import AnthillSale


class Command(BaseCommand):
    help = 'Recalculate all sale balances using credit-payment matching logic'

    def handle(self, *args, **options):
        def match_credits(sale):
            payments = [(p.amount, p.payment_type) for p in sale.payments.all() if not p.ignored]
            credits, positives = [], []
            other_negatives = Decimal('0')
            for amount, ptype in payments:
                ptype = ptype or ''
                amount = amount or Decimal('0')
                if 'credit' in ptype.lower() and amount < 0:
                    credits.append(abs(amount))
                elif amount > 0:
                    positives.append(amount)
                elif amount < 0:
                    other_negatives += amount
            # ALL credits are discount
            discount = sum(credits, Decimal('0'))
            # Match credits to positives — matched positives removed from paid
            unmatched_pos = list(positives)
            for ca in credits:
                for i, pa in enumerate(unmatched_pos):
                    if abs(pa - ca) < Decimal('0.50'):
                        unmatched_pos.pop(i)
                        break
            total_paid = sum(unmatched_pos, Decimal('0')) + other_negatives
            return max(total_paid, Decimal('0')), discount

        updated = 0
        total = 0
        for sale in AnthillSale.objects.filter(sale_value__gt=0).prefetch_related('payments'):
            total += 1
            tp, disc = match_credits(sale)
            sv = sale.sale_value or Decimal('0')
            nb = max(sv - disc - tp, Decimal('0'))
            npif = nb <= Decimal('0')
            if sale.discount != disc or sale.balance_payable != nb or sale.paid_in_full != npif:
                sale.discount = disc
                sale.balance_payable = nb
                sale.paid_in_full = npif
                sale.save(update_fields=['discount', 'balance_payable', 'paid_in_full'])
                updated += 1

        self.stdout.write(self.style.SUCCESS(f'Updated {updated} of {total} sales'))
