import os
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db import DatabaseError, ProgrammingError
from django.db.models import Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST
from PIL import Image

from .gallery_views import make_thumbnail
from .models import Customer, FitterUploadPhoto, FitterUploadSubmission, GalleryImage, Order


MAX_UPLOAD_FILES = 20
MAX_IMAGE_MB = 15
RATE_LIMIT_PER_HOUR = 30
logger = logging.getLogger(__name__)


def _get_client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def _rate_limit_key(request):
    ip = _get_client_ip(request) or 'unknown'
    hour_key = timezone.now().strftime('%Y%m%d%H')
    return f'fitter-upload:{ip}:{hour_key}'


def _is_rate_limited(request):
    key = _rate_limit_key(request)
    try:
        count = cache.get(key, 0)
        if count >= RATE_LIMIT_PER_HOUR:
            return True
        cache.set(key, count + 1, timeout=60 * 60)
        return False
    except Exception:
        # Fail-open if cache backend is unavailable.
        logger.exception('Upload rate-limit cache error')
        return False


def _validate_image(file_obj):
    if file_obj.size > MAX_IMAGE_MB * 1024 * 1024:
        return f'Each image must be under {MAX_IMAGE_MB}MB.'

    name = (file_obj.name or '').lower()
    if not name.endswith(('.jpg', '.jpeg', '.png', '.webp', '.heic')):
        return 'Supported image formats: JPG, PNG, WEBP, HEIC.'

    try:
        img = Image.open(file_obj)
        img.verify()
        file_obj.seek(0)
    except Exception:
        return 'One or more files are not valid images.'

    return ''


def _clone_file(image_field, fallback_name='image.jpg'):
    image_field.open('rb')
    data = image_field.read()
    image_field.close()
    filename = os.path.basename(image_field.name) or fallback_name
    return filename, ContentFile(data)


@require_http_methods(['GET', 'POST'])
def fitter_upload(request):
    """Public page for fitters to upload completed-job photos."""
    if request.method == 'GET':
        return render(request, 'stock_take/upload.html')

    # Honeypot trap for basic bots.
    if request.POST.get('website', '').strip():
        return redirect('fitter_upload')

    if _is_rate_limited(request):
        return HttpResponseBadRequest('Too many uploads from this connection. Please try again later.')

    customer_name = (request.POST.get('customer_name') or '').strip()
    sale_number = (request.POST.get('sale_number') or '').strip()
    files = request.FILES.getlist('images')

    if not customer_name:
        messages.error(request, 'Customer name is required.')
        return redirect('fitter_upload')

    if not files:
        messages.error(request, 'Please select at least one image.')
        return redirect('fitter_upload')

    if len(files) > MAX_UPLOAD_FILES:
        messages.error(request, f'Please upload up to {MAX_UPLOAD_FILES} images at a time.')
        return redirect('fitter_upload')

    for f in files:
        err = _validate_image(f)
        if err:
            messages.error(request, err)
            return redirect('fitter_upload')

    try:
        submission = FitterUploadSubmission.objects.create(
            customer_name=customer_name,
            sale_number=sale_number,
            submitted_ip=_get_client_ip(request),
            submitted_user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
        )
    except (ProgrammingError, DatabaseError):
        logger.exception('Upload staging table unavailable during fitter upload')
        messages.error(request, 'Upload service is temporarily unavailable. Please contact the office.')
        return redirect('fitter_upload')
    except Exception:
        logger.exception('Unexpected error creating fitter upload submission')
        messages.error(request, 'Upload failed due to a server error. Please try again.')
        return redirect('fitter_upload')

    created = 0
    try:
        for f in files:
            try:
                thumb = make_thumbnail(f)
            except Exception:
                logger.exception('Thumbnail generation failed for %s', f.name)
                messages.error(request, 'One image could not be processed. Please re-save it as JPG/PNG and try again.')
                raise

            f.seek(0)
            FitterUploadPhoto.objects.create(
                submission=submission,
                image=f,
                thumbnail=thumb,
                original_name=(f.name or '')[:255],
            )
            created += 1
    except Exception:
        # Keep DB tidy if photo creation fails mid-request.
        submission.delete()
        return redirect('fitter_upload')

    if created == 0:
        messages.error(request, 'No images were uploaded. Please try again.')
        return redirect('fitter_upload')

    messages.success(request, 'Thanks, photos uploaded successfully.')
    return redirect('fitter_upload')


@login_required
def upload_staging(request):
    """Authenticated review queue for fitter uploads."""
    pending = list(
        FitterUploadSubmission.objects.filter(status=FitterUploadSubmission.STATUS_PENDING)
        .prefetch_related('photos')
        .order_by('-created_at')
    )

    for sub in pending:
        q = Q(name__icontains=sub.customer_name) | Q(first_name__icontains=sub.customer_name) | Q(last_name__icontains=sub.customer_name)
        sub.customer_matches = list(Customer.objects.filter(q).order_by('last_name', 'first_name')[:20])
        sub.order_match = None
        if sub.sale_number:
            sub.order_match = Order.objects.select_related('customer').filter(sale_number__iexact=sub.sale_number).first()

    return render(request, 'stock_take/upload_staging.html', {
        'pending_submissions': pending,
    })


@login_required
@require_POST
def upload_staging_publish(request, submission_id):
    submission = get_object_or_404(FitterUploadSubmission, id=submission_id)
    if submission.status != FitterUploadSubmission.STATUS_PENDING:
        messages.warning(request, 'This submission has already been reviewed.')
        return redirect('upload_staging')

    customer = None
    order = None

    customer_id = (request.POST.get('customer_id') or '').strip()
    sale_number = (request.POST.get('sale_number') or '').strip()

    if customer_id:
        customer = Customer.objects.filter(id=customer_id).first()

    if sale_number:
        order = Order.objects.select_related('customer').filter(sale_number__iexact=sale_number).first()
        if order and not customer:
            customer = order.customer

    if not customer:
        messages.error(request, 'Please choose a customer before publishing to gallery.')
        return redirect('upload_staging')

    created = 0
    for photo in submission.photos.all():
        gallery_image = GalleryImage(
            caption='',
            order=order,
            customer=customer,
            uploaded_by=request.user,
        )

        image_name, image_file = _clone_file(photo.image, fallback_name=f'stage_{photo.id}.jpg')
        gallery_image.image.save(image_name, image_file, save=False)

        if photo.thumbnail:
            thumb_name, thumb_file = _clone_file(photo.thumbnail, fallback_name=f'stage_{photo.id}_thumb.jpg')
            gallery_image.thumbnail.save(thumb_name, thumb_file, save=False)

        gallery_image.save()
        photo.gallery_image = gallery_image
        photo.save(update_fields=['gallery_image'])
        created += 1

    submission.status = FitterUploadSubmission.STATUS_PUBLISHED
    submission.linked_customer = customer
    submission.linked_order = order
    submission.reviewed_by = request.user
    submission.reviewed_at = timezone.now()
    submission.save(update_fields=['status', 'linked_customer', 'linked_order', 'reviewed_by', 'reviewed_at'])

    messages.success(request, f'Published {created} photo(s) to Gallery.')
    return redirect('upload_staging')


@login_required
@require_POST
def upload_staging_reject(request, submission_id):
    submission = get_object_or_404(FitterUploadSubmission, id=submission_id)
    if submission.status == FitterUploadSubmission.STATUS_PENDING:
        submission.status = FitterUploadSubmission.STATUS_REJECTED
        submission.reviewed_by = request.user
        submission.reviewed_at = timezone.now()
        submission.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])
        messages.info(request, 'Submission rejected.')
    return redirect('upload_staging')
