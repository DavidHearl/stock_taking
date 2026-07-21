"""Endpoints behind the "Arrange Install Date & Take Stock Payment" step.

Both actions on that stage card live here so the step can be completed without
leaving the sale page:

* ``book_order_fit_date`` — the fit board is still where jobs are dragged around
  the calendar, but this books a date in place. Unlike a calendar drag (which
  leaves the appointment provisional), booking here is a deliberate confirmation,
  so the appointment is written as confirmed.
* ``set_order_fit_confirmed`` — the install-date tick, which is the calendar's
  provisional flag seen from the sale page. Unticking it puts the date back to
  provisional; ticking it confirms it. Both are the same edit the fit board makes.
* ``set_order_stock_payment`` — a manual override for the payment tick. Not every
  stock payment reaches Atlas as a typed payment row, and the step shouldn't be
  un-completable because of that.
"""

import json
import logging
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from .models import FitAppointment, Fitter, Order

logger = logging.getLogger(__name__)


@login_required
@require_POST
def book_order_fit_date(request, order_id):
	"""Set (or move) the confirmed fit appointment for an order.

	An order has at most one appointment, so an existing one is updated in place
	rather than duplicated — matching how the calendar's own move/drag handling
	works. ``Order.fit_date`` and the linked Anthill sale are kept in step so the
	hero card, fit board and sale list all agree.
	"""
	order = get_object_or_404(Order, id=order_id)

	try:
		data = json.loads(request.body or '{}')
	except ValueError:
		return JsonResponse({'error': 'Invalid request body'}, status=400)

	fit_date_str = (data.get('fit_date') or '').strip()
	if not fit_date_str:
		return JsonResponse({'error': 'A fit date is required'}, status=400)
	try:
		fit_date = datetime.strptime(fit_date_str, '%Y-%m-%d').date()
	except ValueError:
		return JsonResponse({'error': 'Fit date must be in YYYY-MM-DD format'}, status=400)

	appointment = order.fit_appointments.order_by('id').first()

	# Valid fitters come from the Fitter table (what the fit board offers), not
	# FitAppointment.FITTER_CHOICES — that constant is stale and rejects codes
	# already in live use. An appointment's existing fitter is always allowed so
	# re-booking a legacy code doesn't fail.
	fitter = (data.get('fitter') or '').strip()
	fitter_names = dict(
		Fitter.objects.filter(active=True).exclude(code='').values_list('code', 'name')
	)
	allowed = set(fitter_names) | ({appointment.fitter} if appointment else set())
	if not fitter or fitter not in allowed:
		return JsonResponse({'error': 'Select a fitter'}, status=400)

	starts_pm = bool(data.get('starts_pm', False))

	try:
		from .views import _order_fit_duration

		if appointment:
			appointment.fit_date = fit_date
			appointment.fitter = fitter
			appointment.starts_pm = starts_pm
			appointment.is_provisional = False
			appointment.save(update_fields=['fit_date', 'fitter', 'starts_pm', 'is_provisional'])
		else:
			appointment = FitAppointment.objects.create(
				order=order,
				fit_date=fit_date,
				fitter=fitter,
				starts_pm=starts_pm,
				is_provisional=False,
				fit_duration=_order_fit_duration(order) or 1,
			)

		if order.fit_date != fit_date:
			order.fit_date = fit_date
			order.save(update_fields=['fit_date'])

		sale = order.anthill_sale.first()
		if sale and sale.fit_date != fit_date:
			sale.fit_date = fit_date
			sale.save(update_fields=['fit_date'])
	except Exception as exc:
		logger.error('Failed to book fit date for order %s: %s', order_id, exc)
		return JsonResponse({'error': 'Could not save the fit date'}, status=500)

	return JsonResponse({
		'success': True,
		'appointment_id': appointment.id,
		'fit_date': fit_date.strftime('%Y-%m-%d'),
		'fit_date_display': fit_date.strftime('%d %b %Y'),
		'fitter': appointment.fitter,
		'fitter_display': fitter_names.get(appointment.fitter, appointment.fitter),
	})


@login_required
@require_POST
def set_order_stock_payment(request, order_id):
	"""Tick or untick the stock payment by hand.

	The tick is normally derived from a payment typed as a stock payment on the
	sale, but plenty are taken without ever landing in Atlas that way. This flag
	is a plain override so the step can be completed regardless — it never
	overrides a payment that *is* recorded.
	"""
	order = get_object_or_404(Order, id=order_id)

	try:
		data = json.loads(request.body or '{}')
	except ValueError:
		return JsonResponse({'error': 'Invalid request body'}, status=400)

	confirmed = bool(data.get('confirmed'))
	if order.stock_payment_confirmed != confirmed:
		order.stock_payment_confirmed = confirmed
		order.save(update_fields=['stock_payment_confirmed'])

	return JsonResponse({'success': True, 'confirmed': order.stock_payment_confirmed})


@login_required
@require_POST
def set_order_fit_confirmed(request, order_id):
	"""Confirm the order's fit date, or put it back to provisional.

	This is the same flag the fit board's confirm action sets — a date dragged
	onto the calendar stays provisional until someone confirms it, and a date
	that turns out to be tentative can be dropped back here. There has to be an
	appointment to act on: an install date can't be confirmed before one exists.
	"""
	order = get_object_or_404(Order, id=order_id)

	try:
		data = json.loads(request.body or '{}')
	except ValueError:
		return JsonResponse({'error': 'Invalid request body'}, status=400)

	appointment = order.fit_appointments.order_by('id').first()
	if not appointment:
		return JsonResponse({'error': 'Book a fit date first'}, status=400)

	confirmed = bool(data.get('confirmed'))
	if appointment.is_provisional == confirmed:
		appointment.is_provisional = not confirmed
		appointment.save(update_fields=['is_provisional'])

	return JsonResponse({
		'success': True,
		'confirmed': not appointment.is_provisional,
		'fit_date': appointment.fit_date.strftime('%Y-%m-%d'),
	})
