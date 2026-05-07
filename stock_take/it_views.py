from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.db.models import Sum
from django.contrib.auth.models import User
from .models import MobileDevice, PhoneTemplate
from .permissions import page_permission_required


@login_required
@page_permission_required('mobile_devices')
def mobile_phone_templates(request):
    """Return phone templates as JSON for the add-device modal."""
    templates = list(PhoneTemplate.objects.values(
        'id', 'name', 'device_type', 'model', 'chip', 'security_updates_until'
    ))
    return JsonResponse({'templates': templates})


@login_required
@page_permission_required('mobile_devices', action='create')
def mobile_phone_template_create(request):
    """Create a new phone template."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    name = request.POST.get('name', '').strip()
    if not name:
        return JsonResponse({'error': 'Name is required'}, status=400)
    tpl = PhoneTemplate.objects.create(
        name=name,
        device_type=request.POST.get('device_type', 'iphone'),
        model=request.POST.get('model', '').strip(),
        chip=request.POST.get('chip', '').strip(),
        security_updates_until=request.POST.get('security_updates_until', '').strip(),
    )
    return JsonResponse({
        'success': True,
        'template': {
            'id': tpl.id,
            'name': tpl.name,
            'device_type': tpl.device_type,
            'model': tpl.model,
            'chip': tpl.chip,
            'security_updates_until': tpl.security_updates_until,
        }
    })


@login_required
@page_permission_required('mobile_devices')
def mobile_devices(request):
    """IT – Mobile device and SIM register."""
    # Physical phones only (no eSIMs)
    phones_active = MobileDevice.objects.filter(
        is_dead=False, is_esim=False
    ).exclude(phone_number='').select_related('assigned_user')

    phones_spare = MobileDevice.objects.filter(
        is_dead=False, is_esim=False, phone_number=''
    ).select_related('assigned_user')

    phones_dead = MobileDevice.objects.filter(
        is_dead=True, is_esim=False
    ).select_related('assigned_user')

    # SIMs tab – all active SIM records (phones with SIM + eSIMs)
    esims = MobileDevice.objects.filter(
        is_dead=False, is_esim=True
    ).select_related('assigned_user')

    from itertools import chain
    sims_active = list(chain(phones_active, esims))

    active_sim_total = phones_active.aggregate(total=Sum('sim_cost'))['total'] or 0
    esim_total = esims.aggregate(total=Sum('sim_cost'))['total'] or 0
    active_sim_total += esim_total

    atlas_users = User.objects.filter(is_active=True).order_by('first_name', 'last_name')

    context = {
        'phones_active': phones_active,
        'phones_spare': phones_spare,
        'phones_dead': phones_dead,
        'sims_active': sims_active,
        'active_sim_total': '{:.2f}'.format(active_sim_total),
        # kept for Move SIM modal target lists
        'spare_devices': phones_spare,
        'dead_devices': phones_dead,
        'atlas_users': atlas_users,
    }
    return render(request, 'stock_take/it_mobile.html', context)


@login_required
@page_permission_required('mobile_devices', action='edit')
def mobile_device_save(request, device_id):
    """Save changes to a single mobile device row (AJAX)."""
    device = get_object_or_404(MobileDevice, id=device_id)

    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    fields = [
        'device_type', 'model', 'chip', 'serial_number', 'condition',
        'status', 'purchase_date', 'security_updates_until',
        'phone_number', 'notes',
    ]
    for field in fields:
        value = request.POST.get(field)
        if value is not None:
            setattr(device, field, value.strip())

    assigned_user_id = request.POST.get('assigned_user_id', '').strip()
    if assigned_user_id:
        device.assigned_user = User.objects.filter(pk=assigned_user_id).first()
    else:
        device.assigned_user = None

    sim_cost_raw = request.POST.get('sim_cost', '').strip()
    if sim_cost_raw:
        try:
            device.sim_cost = float(sim_cost_raw.replace('£', '').replace(',', ''))
        except ValueError:
            pass
    else:
        device.sim_cost = None

    device.is_dead = request.POST.get('is_dead') == 'true'
    device.is_esim = request.POST.get('is_esim') == 'true'
    device.save()

    return JsonResponse({'success': True})


@login_required
@page_permission_required('mobile_devices', action='create')
def mobile_device_create(request):
    """Create a new mobile device record."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    assigned_user = None
    assigned_user_id = request.POST.get('assigned_user_id', '').strip()
    if assigned_user_id:
        assigned_user = User.objects.filter(pk=assigned_user_id).first()

    device = MobileDevice.objects.create(
        device_type=request.POST.get('device_type', ''),
        model=request.POST.get('model', '').strip(),
        chip=request.POST.get('chip', '').strip(),
        serial_number=request.POST.get('serial_number', '').strip(),
        condition=request.POST.get('condition', ''),
        status=request.POST.get('status', 'active'),
        purchase_date=request.POST.get('purchase_date', '').strip(),
        security_updates_until=request.POST.get('security_updates_until', '').strip(),
        phone_number=request.POST.get('phone_number', '').strip(),
        assigned_user=assigned_user,
        notes=request.POST.get('notes', '').strip(),
        is_dead=request.POST.get('is_dead') == 'true',
        is_esim=request.POST.get('is_esim') == 'true',
    )

    sim_cost_raw = request.POST.get('sim_cost', '').strip()
    if sim_cost_raw:
        try:
            device.sim_cost = float(sim_cost_raw.replace('£', '').replace(',', ''))
            device.save(update_fields=['sim_cost'])
        except ValueError:
            pass

    messages.success(request, 'Device added.')
    return redirect('mobile_devices')


@login_required
@page_permission_required('mobile_devices', action='delete')
def mobile_device_delete(request, device_id):
    """Delete a mobile device record."""
    device = get_object_or_404(MobileDevice, id=device_id)
    device.delete()
    messages.success(request, 'Device deleted.')
    return redirect('mobile_devices')


@login_required
@page_permission_required('mobile_devices', action='edit')
def mobile_sim_transfer(request, device_id):
    """Transfer the SIM (phone_number, sim_cost) from one device to another."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    source = get_object_or_404(MobileDevice, id=device_id)
    target_id = request.POST.get('target_device_id', '').strip()
    if not target_id:
        return JsonResponse({'error': 'No target device specified'}, status=400)

    target = get_object_or_404(MobileDevice, id=target_id)

    # Move SIM fields to target
    target.phone_number = source.phone_number
    target.sim_cost = source.sim_cost
    target.is_esim = source.is_esim
    target.assigned_user = source.assigned_user
    target.status = 'active'
    target.is_dead = False
    target.save(update_fields=['phone_number', 'sim_cost', 'is_esim', 'assigned_user', 'status', 'is_dead'])

    # Clear SIM from source
    source.phone_number = ''
    source.sim_cost = None
    source.assigned_user = None
    source.status = 'spare'
    source.save(update_fields=['phone_number', 'sim_cost', 'assigned_user', 'status'])

    return JsonResponse({'success': True})


# ─── Laptop & Desktop placeholders ───────────────────────────────────────────

@login_required
@page_permission_required('laptop_devices')
def laptop_devices(request):
    return render(request, 'stock_take/it_laptops.html', {})


@login_required
@page_permission_required('desktop_devices')
def desktop_devices(request):
    return render(request, 'stock_take/it_desktops.html', {})
