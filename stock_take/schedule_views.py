"""Mobile-friendly weekly schedule for the logged-in fitter."""

from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import render
from django.utils import timezone

from .models import FitAppointment, Fitter


def get_user_fitter(user):
    """Return the active Fitter linked to this user, or None."""
    if not user.is_authenticated:
        return None
    return Fitter.objects.filter(user=user).first()


def _is_admin(user):
    """True for superusers or users with the admin role."""
    if user.is_superuser:
        return True
    profile = getattr(user, 'profile', None)
    return bool(profile and profile.role and profile.role.name == 'admin')


def _job_contact(order):
    """Return the fitter-facing phone number and full address for a job."""
    if order is None:
        return {'phone': '', 'address': ''}

    customer = order.customer
    phone = ''
    if customer:
        phone = (customer.phone or '').strip() or (customer.fax or '').strip()

    parts = []
    if order.address:
        parts.append(order.address.strip())
    elif customer:
        for field in (customer.address, customer.address_1, customer.address_2):
            if field and field.strip():
                parts.append(field.strip())

    existing = ' '.join(parts).lower()
    city = (customer.city.strip() if customer and customer.city else '')
    if city and city.lower() not in existing:
        parts.append(city)

    postcode = (order.postcode or '').strip()
    if not postcode and customer and customer.postcode:
        postcode = customer.postcode.strip()
    if postcode and postcode.lower() not in existing:
        parts.append(postcode)

    return {'phone': phone, 'address': ', '.join(p for p in parts if p)}


@login_required
def fitter_schedule(request):
    """Show this week's fit appointments for the logged-in fitter.

    Fitters see their own schedule. Admins/superusers can pick any fitter from
    a dropdown. Supports moving forward/back a week via the ?week=<offset>
    query parameter.
    """
    is_admin = _is_admin(request.user)
    own_fitter = get_user_fitter(request.user)

    fitter_options = []
    if is_admin:
        fitter_options = list(
            Fitter.objects.filter(active=True).exclude(code='').order_by('name')
        )

    fitter = own_fitter
    if is_admin:
        selected_id = request.GET.get('fitter')
        if selected_id:
            fitter = Fitter.objects.filter(id=selected_id).first() or fitter
        if fitter is None and fitter_options:
            fitter = fitter_options[0]

    if fitter is None:
        return HttpResponseForbidden('No fitter is linked to your account.')

    try:
        week_offset = int(request.GET.get('week', 0))
    except (TypeError, ValueError):
        week_offset = 0
    # Keep navigation within a sensible range.
    week_offset = max(-52, min(52, week_offset))

    today = timezone.localdate()
    week_start = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
    week_end = week_start + timedelta(days=6)

    appointments = []
    if fitter.code:
        # Look back far enough to catch multi-day jobs that started before the
        # week but span into it.
        appointments = list(
            FitAppointment.objects
            .filter(fitter=fitter.code, fit_date__lte=week_end, fit_date__gte=week_start - timedelta(days=60))
            .select_related('order', 'order__customer', 'remedial')
            .order_by('fit_date', 'order__last_name')
        )

    by_date = {}
    unique_ids = set()
    for appt in appointments:
        duration = max(1, appt.fit_duration or 1)
        start_date = appt.fit_date
        end_date = start_date + timedelta(days=duration - 1)
        if end_date < week_start or start_date > week_end:
            continue
        unique_ids.add(appt.id)
        contact = _job_contact(appt.order)
        # Place the job on every day it spans that falls inside this week.
        day = max(start_date, week_start)
        last = min(end_date, week_end)
        while day <= last:
            by_date.setdefault(day, []).append({
                'appt': appt,
                'order': appt.order,
                'phone': contact['phone'],
                'address': contact['address'],
                'is_multi_day': duration > 1,
                'day_index': (day - start_date).days + 1,
                'total_days': duration,
            })
            day += timedelta(days=1)

    days = []
    for i in range(7):
        day = week_start + timedelta(days=i)
        days.append({
            'date': day,
            'is_today': day == today,
            'appointments': by_date.get(day, []),
        })

    return render(request, 'stock_take/fitter_schedule.html', {
        'fitter': fitter,
        'is_admin': is_admin,
        'fitter_options': fitter_options,
        'selected_fitter_id': fitter.id,
        'days': days,
        'week_start': week_start,
        'week_end': week_end,
        'week_offset': week_offset,
        'prev_week': week_offset - 1,
        'next_week': week_offset + 1,
        'is_current_week': week_offset == 0,
        'appointment_count': len(unique_ids),
    })
