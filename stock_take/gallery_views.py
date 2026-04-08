from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q
from django.core.files.base import ContentFile
from PIL import Image, ImageOps
import io
import os
from .models import GalleryImage, Order, Customer


def make_thumbnail(image_file, max_width=480):
    """Create a resized thumbnail from an uploaded image file."""
    img = Image.open(image_file)
    img = ImageOps.exif_transpose(img)
    if img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')
    if img.width > max_width:
        ratio = max_width / img.width
        new_size = (max_width, int(img.height * ratio))
        img = img.resize(new_size, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=75)
    buf.seek(0)
    name = os.path.splitext(os.path.basename(image_file.name))[0]
    return ContentFile(buf.read(), name=f'{name}_thumb.jpg')


@login_required
def gallery(request):
    """Display the photo gallery with optional filtering"""
    images = GalleryImage.objects.select_related('order', 'customer', 'uploaded_by').all()

    search = request.GET.get('search', '').strip()
    filter_type = request.GET.get('filter', '')

    if search:
        images = images.filter(
            Q(caption__icontains=search) |
            Q(order__sale_number__icontains=search) |
            Q(order__first_name__icontains=search) |
            Q(order__last_name__icontains=search) |
            Q(customer__name__icontains=search) |
            Q(customer__first_name__icontains=search) |
            Q(customer__last_name__icontains=search)
        )

    if filter_type == 'assigned':
        images = images.filter(Q(order__isnull=False) | Q(customer__isnull=False))
    elif filter_type == 'unassigned':
        images = images.filter(order__isnull=True, customer__isnull=True)

    images = images.order_by('-uploaded_at')

    return render(request, 'stock_take/gallery.html', {
        'images': images,
        'search': search,
        'filter_type': filter_type,
    })


@login_required
def gallery_upload(request):
    """Upload one or more images to the gallery"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)

    files = request.FILES.getlist('images')
    if not files:
        return JsonResponse({'success': False, 'error': 'No files provided'}, status=400)

    order_id = request.POST.get('order_id') or None
    customer_id = request.POST.get('customer_id') or None
    caption = request.POST.get('caption', '')

    order = None
    customer = None
    if order_id:
        order = Order.objects.filter(id=order_id).first()
        if order and order.customer:
            customer = order.customer
    if customer_id and not customer:
        customer = Customer.objects.filter(id=customer_id).first()

    created = []
    for f in files:
        thumb = make_thumbnail(f)
        f.seek(0)
        img = GalleryImage.objects.create(
            image=f,
            thumbnail=thumb,
            caption=caption,
            order=order,
            customer=customer,
            uploaded_by=request.user,
        )
        created.append(img.id)

    return JsonResponse({'success': True, 'count': len(created), 'ids': created})


@login_required
def gallery_delete(request, image_id):
    """Delete a gallery image"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)

    image = get_object_or_404(GalleryImage, id=image_id)
    image.image.delete(save=False)
    image.delete()
    return JsonResponse({'success': True})


@login_required
def gallery_update(request, image_id):
    """Update caption / assignment of a gallery image"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)

    image = get_object_or_404(GalleryImage, id=image_id)

    caption = request.POST.get('caption')
    if caption is not None:
        image.caption = caption

    order_id = request.POST.get('order_id')
    customer_id = request.POST.get('customer_id')

    # Allow clearing assignments by sending empty string
    if order_id is not None:
        if order_id == '':
            image.order = None
        else:
            image.order = Order.objects.filter(id=order_id).first()

    if customer_id is not None:
        if customer_id == '':
            image.customer = None
        else:
            image.customer = Customer.objects.filter(id=customer_id).first()

    # Auto-link customer from order if not explicitly set
    if image.order and not image.customer and image.order.customer:
        image.customer = image.order.customer

    image.save()
    return JsonResponse({'success': True})


@login_required
def gallery_customer_orders(request, customer_id):
    """Return orders for a given customer (AJAX)"""
    orders = Order.objects.filter(customer_id=customer_id).order_by('-order_date').values(
        'id', 'sale_number', 'first_name', 'last_name', 'total_value_inc_vat', 'order_date'
    )
    return JsonResponse({'orders': list(orders)})


def _rotate_image_file(image_field, angle):
    """Rotate an image stored in a file field and return a ContentFile."""
    img = Image.open(image_field)
    img = ImageOps.exif_transpose(img)
    if img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')
    img = img.rotate(angle, expand=True)
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=90)
    buf.seek(0)
    name = os.path.splitext(os.path.basename(image_field.name))[0]
    return ContentFile(buf.read(), name=f'{name}.jpg')


@login_required
def gallery_rotate(request, image_id):
    """Rotate a gallery image 90° clockwise and regenerate thumbnail"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)

    image = get_object_or_404(GalleryImage, id=image_id)
    direction = request.POST.get('direction', 'cw')
    angle = -90 if direction == 'cw' else 90

    # Rotate full image
    rotated = _rotate_image_file(image.image, angle)
    old_image_name = image.image.name
    image.image.save(os.path.basename(old_image_name), rotated, save=False)

    # Regenerate thumbnail from the new full image
    image.image.open()
    thumb = make_thumbnail(image.image)
    old_thumb_name = image.thumbnail.name if image.thumbnail else ''
    thumb_name = os.path.basename(old_thumb_name) if old_thumb_name else f'img_{image_id}_thumb.jpg'
    image.thumbnail.save(thumb_name, thumb, save=False)

    image.save()

    return JsonResponse({
        'success': True,
        'image_url': image.image.url,
        'thumbnail_url': image.thumbnail.url if image.thumbnail else image.image.url,
    })

