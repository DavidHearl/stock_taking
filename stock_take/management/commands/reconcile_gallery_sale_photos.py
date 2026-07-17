"""
Management command: reconcile_gallery_sale_photos
─────────────────────────────────────────────────
Keep the photo gallery and the sale-detail photo card in sync.

Both surfaces read the SAME ``GalleryImage`` rows — there is no separate "sale
photo" store. The gallery page lists every ``GalleryImage``; a sale's photo card
lists only the subset where ``GalleryImage.order == sale.order``
(see ``customer_views._build_sale_context``). So the two diverge in two ways:

  A. Gallery photo missing from its sale — a ``GalleryImage`` that has a
     ``customer`` but a blank ``order`` (e.g. a fitter upload published with only
     a customer chosen, or a gallery upload assigned to a customer but no order).
     It appears in the gallery but never on the sale page. Fix: fill ``order``
     when it can be resolved unambiguously, and mirror ``customer`` from a set
     ``order`` so the photo also surfaces under the customer.

  B. "Sale photo" missing from the gallery — a fitter uploaded photos against a
     sale number, but the submission is still PENDING in staging
     (``FitterUploadPhoto``), so the photos never reached the gallery or the
     sale. Fix: publish staged photos into the gallery when the submission's
     ``sale_number`` matches exactly one ``Order`` (the same confidence bar the
     manual review flow uses).

The command only ever FILLS BLANKS and only links when the match is
unambiguous — it never overwrites an existing ``order``/``customer`` and never
guesses between multiple candidate orders. Ambiguous rows are reported for
manual review rather than touched.

Usage:
    python manage.py reconcile_gallery_sale_photos                 # dry-run (default)
    python manage.py reconcile_gallery_sale_photos --sale-number 425306
    python manage.py reconcile_gallery_sale_photos --skip-staging  # phase A only
    python manage.py reconcile_gallery_sale_photos --fix           # apply changes
"""

import os

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from stock_take.models import (
	FitterUploadSubmission,
	GalleryImage,
	Order,
)


class Command(BaseCommand):
	help = (
		'Reconcile the photo gallery with sale photo cards — link gallery images '
		'to their order/customer and publish orphaned fitter uploads.'
	)

	def add_arguments(self, parser):
		parser.add_argument(
			'--sale-number',
			type=str,
			default=None,
			help='Only reconcile photos/submissions for this single sale_number',
		)
		parser.add_argument(
			'--skip-staging',
			action='store_true',
			help='Skip phase B (publishing pending fitter uploads)',
		)
		parser.add_argument(
			'--fix',
			action='store_true',
			help='Actually apply the changes (default: dry-run)',
		)

	def handle(self, *args, **options):
		self.fix = options['fix']
		self.sn_filter = (options.get('sale_number') or '').strip() or None
		skip_staging = options['skip_staging']

		mode = self.style.SUCCESS('APPLY') if self.fix else self.style.WARNING('DRY-RUN')
		self.stdout.write(f'{mode} — reconciling gallery images with sale photos\n')

		with transaction.atomic():
			a = self._reconcile_gallery_images()
			b = (0, 0, 0) if skip_staging else self._publish_pending_staging()
			if not self.fix:
				transaction.set_rollback(True)

		linked_order, mirrored_customer, ambiguous = a
		published_subs, published_photos, unmatched_subs = b

		self.stdout.write('')
		self.stdout.write('Phase A — gallery image links:')
		self.stdout.write(f'  linked order from customer : {linked_order}')
		self.stdout.write(f'  mirrored customer from order: {mirrored_customer}')
		self.stdout.write(self.style.WARNING(f'  ambiguous (needs review)   : {ambiguous}'))
		if not skip_staging:
			self.stdout.write('Phase B — pending fitter uploads:')
			self.stdout.write(f'  submissions published      : {published_subs}')
			self.stdout.write(f'  photos published to gallery: {published_photos}')
			self.stdout.write(self.style.WARNING(f'  unmatched (needs review)   : {unmatched_subs}'))

		self.stdout.write('')
		if self.fix:
			self.stdout.write(self.style.SUCCESS('Done — changes applied.'))
		else:
			self.stdout.write(self.style.WARNING(
				'Dry-run — no changes written. Re-run with --fix to apply.'
			))

	# ── Phase A: link existing gallery images ────────────────────────────────
	def _reconcile_gallery_images(self):
		"""Fill blank order/customer on GalleryImage rows where unambiguous."""
		qs = GalleryImage.objects.select_related('order', 'customer')
		if self.sn_filter:
			# Limit to images already on the target order, or unassigned images
			# whose customer owns an order with that sale_number.
			owners = Order.objects.filter(sale_number__iexact=self.sn_filter)
			customer_ids = list(
				owners.filter(customer__isnull=False).values_list('customer_id', flat=True)
			)
			qs = qs.filter(
				Q(order__in=owners)
				| Q(order__isnull=True, customer_id__in=customer_ids)
			).distinct()

		linked_order = mirrored_customer = ambiguous = 0

		for img in qs:
			# order set, customer blank → mirror the order's customer so the photo
			# also surfaces under the customer and in customer-name gallery search.
			if img.order_id and not img.customer_id and img.order.customer_id:
				self.stdout.write(
					f'  GalleryImage {img.id}: set customer = {img.order.customer_id} '
					f'(from order {img.order_id})'
				)
				if self.fix:
					img.customer = img.order.customer
					img.save(update_fields=['customer'])
				mirrored_customer += 1
				continue

			# customer set, order blank → resolve the order if we can do so safely.
			if img.customer_id and not img.order_id:
				order = self._resolve_order_for_image(img)
				if order is None:
					ambiguous += 1
					continue
				self.stdout.write(
					f'  GalleryImage {img.id}: set order = {order.id} '
					f'(sale {order.sale_number}) for customer {img.customer_id}'
				)
				if self.fix:
					img.order = order
					img.save(update_fields=['order'])
				linked_order += 1

		return linked_order, mirrored_customer, ambiguous

	def _resolve_order_for_image(self, img):
		"""Best unambiguous Order for a customer-only image, else None.

		1. If the caption names exactly one of the customer's sale_numbers, use it.
		2. Else, if the customer has exactly one order, use that.
		3. Otherwise it is ambiguous — leave it for manual review.
		"""
		orders = list(Order.objects.filter(customer_id=img.customer_id))
		if not orders:
			return None

		caption = (img.caption or '')
		if caption:
			matches = [
				o for o in orders
				if o.sale_number and o.sale_number in caption
			]
			if len(matches) == 1:
				return matches[0]

		if len(orders) == 1:
			return orders[0]

		return None

	# ── Phase B: publish orphaned staged fitter uploads ──────────────────────
	def _publish_pending_staging(self):
		"""Publish pending fitter submissions whose sale_number matches one Order."""
		subs = (
			FitterUploadSubmission.objects
			.filter(status=FitterUploadSubmission.STATUS_PENDING)
			.prefetch_related('photos')
		)
		if self.sn_filter:
			subs = subs.filter(sale_number__iexact=self.sn_filter)

		published_subs = published_photos = unmatched_subs = 0

		for sub in subs:
			order = None
			if sub.sale_number:
				matches = list(
					Order.objects.select_related('customer')
					.filter(sale_number__iexact=sub.sale_number)
				)
				if len(matches) == 1:
					order = matches[0]

			customer = order.customer if order else None
			if order is None or customer is None:
				# No confident sale_number → order match (or the order has no
				# customer). Leave it for the manual review queue in the gallery.
				self.stdout.write(self.style.WARNING(
					f'  Submission {sub.id} ({sub.customer_name!r}, '
					f'sale_number={sub.sale_number!r}): no unambiguous order — skipped'
				))
				unmatched_subs += 1
				continue

			photos = list(sub.photos.all())
			self.stdout.write(
				f'  Submission {sub.id}: publish {len(photos)} photo(s) to gallery '
				f'for order {order.id} (sale {order.sale_number}), customer {customer.id}'
			)

			if self.fix:
				for photo in photos:
					self._publish_staged_photo(photo, order, customer)
				sub.status = FitterUploadSubmission.STATUS_PUBLISHED
				sub.linked_customer = customer
				sub.linked_order = order
				sub.reviewed_at = timezone.now()
				sub.save(update_fields=[
					'status', 'linked_customer', 'linked_order', 'reviewed_at',
				])

			published_subs += 1
			published_photos += len(photos)

		return published_subs, published_photos, unmatched_subs

	def _publish_staged_photo(self, photo, order, customer):
		"""Clone a staged FitterUploadPhoto into a new GalleryImage.

		Mirrors upload_views.upload_staging_publish so published rows are
		identical to the manual-review path.
		"""
		gallery_image = GalleryImage(
			caption='',
			order=order,
			customer=customer,
			uploaded_by=None,
		)

		image_name, image_file = self._clone_file(photo.image, f'stage_{photo.id}.jpg')
		gallery_image.image.save(image_name, image_file, save=False)

		if photo.thumbnail:
			thumb_name, thumb_file = self._clone_file(
				photo.thumbnail, f'stage_{photo.id}_thumb.jpg'
			)
			gallery_image.thumbnail.save(thumb_name, thumb_file, save=False)

		gallery_image.save()
		photo.gallery_image = gallery_image
		photo.save(update_fields=['gallery_image'])

	@staticmethod
	def _clone_file(image_field, fallback_name):
		image_field.open('rb')
		data = image_field.read()
		image_field.close()
		filename = os.path.basename(image_field.name) or fallback_name
		return filename, ContentFile(data)
