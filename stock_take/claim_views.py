from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.db.models import Q, Max
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from collections import OrderedDict
from .models import ClaimDocument
import os
import io
import zipfile
import math

PAGE_SIZE = 100


@login_required
def claim_service(request):
    """Main claim service page with grouped PDFs."""
    query = request.GET.get('q', '').strip()
    page = int(request.GET.get('page', 1))

    documents = ClaimDocument.objects.all()
    if query:
        documents = documents.filter(
            Q(title__icontains=query) |
            Q(customer_name__icontains=query) |
            Q(group_key__icontains=query) |
            Q(file__icontains=query)
        )

    # Group documents by group_key
    grouped = OrderedDict()
    ungrouped = []

    for doc in documents:
        if doc.group_key:
            grouped.setdefault(doc.group_key, []).append(doc)
        else:
            ungrouped.append(doc)

    # Build display-friendly group data
    all_groups = []
    for key, docs in grouped.items():
        parts = key.split('_')
        job_number = parts[0] if parts else key
        customer = parts[1] if len(parts) >= 2 else ''
        job_id = parts[2] if len(parts) >= 3 else ''
        display_name = f"{job_number} - {customer}" if customer else key
        all_groups.append({
            'key': key,
            'display_name': display_name,
            'job_number': job_number,
            'customer': customer,
            'job_id': job_id,
            'documents': docs,
            'count': len(docs),
            'date': docs[0].uploaded_at,
        })

    total_groups = len(all_groups)

    # Paginate only when not searching
    if query:
        groups = all_groups
        total_pages = 1
        page = 1
    else:
        total_pages = max(1, math.ceil(total_groups / PAGE_SIZE))
        page = min(page, total_pages)
        start = (page - 1) * PAGE_SIZE
        groups = all_groups[start:start + PAGE_SIZE]

    context = {
        'groups': groups,
        'ungrouped': ungrouped if (query or page == total_pages) else [],
        'search_query': query,
        'total_count': ClaimDocument.objects.count(),
        'group_count': total_groups,
        'page': page,
        'total_pages': total_pages,
        'showing_count': len(groups),
    }
    return render(request, 'stock_take/claim_service.html', context)


@login_required
def claim_upload(request):
    """Upload a new claim PDF."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    title = request.POST.get('title', '').strip()
    customer_name = request.POST.get('customer_name', '').strip()
    file = request.FILES.get('file')

    if not file:
        return JsonResponse({'error': 'No file provided'}, status=400)

    if not title:
        title = os.path.splitext(file.name)[0]

    # Auto-extract group_key from filename
    group_key = ClaimDocument.extract_group_key(file.name)
    if not customer_name:
        customer_name = ClaimDocument.extract_customer_name(file.name)

    doc = ClaimDocument.objects.create(
        title=title,
        file=file,
        customer_name=customer_name,
        group_key=group_key,
        uploaded_by=request.user,
    )

    return JsonResponse({
        'success': True,
        'document': {
            'id': doc.id,
            'title': doc.title,
            'customer_name': doc.customer_name,
            'filename': doc.filename,
            'url': doc.file.url,
            'uploaded_at': doc.uploaded_at.strftime('%d %b %Y'),
        }
    })


@login_required
def claim_delete(request, doc_id):
    """Delete a claim document."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    doc = get_object_or_404(ClaimDocument, id=doc_id)
    doc.file.delete(save=False)
    doc.delete()

    return JsonResponse({'success': True})


@login_required
def claim_download_zip(request, group_key):
    """Download all PDFs in a group as a zip file."""
    documents = ClaimDocument.objects.filter(group_key=group_key)
    if not documents.exists():
        return JsonResponse({'error': 'No documents found for this group'}, status=404)

    # Create zip in memory
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for doc in documents:
            try:
                doc.file.open('rb')
                zf.writestr(doc.filename, doc.file.read())
                doc.file.close()
            except Exception:
                continue

    buffer.seek(0)
    response = HttpResponse(buffer.read(), content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="{group_key}.zip"'
    return response


@csrf_exempt
def claim_api_upload(request):
    """API endpoint for automated PDF uploads from remote PC.
    Authenticates via X-API-Key header instead of session login.
    Auto-extracts group_key and customer_name from filename pattern.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    api_key = request.headers.get('X-API-Key', '')
    expected_key = getattr(settings, 'CLAIM_UPLOAD_API_KEY', '')
    if not api_key or api_key != expected_key:
        return JsonResponse({'error': 'Invalid API key'}, status=403)

    file = request.FILES.get('file')
    if not file:
        return JsonResponse({'error': 'No file provided'}, status=400)

    title = request.POST.get('title', '').strip()
    customer_name = request.POST.get('customer_name', '').strip()
    group_key = request.POST.get('group_key', '').strip()

    if not title:
        title = os.path.splitext(file.name)[0]

    # Auto-extract group_key and customer_name from filename if not provided
    if not group_key:
        group_key = ClaimDocument.extract_group_key(file.name)
    if not customer_name:
        customer_name = ClaimDocument.extract_customer_name(file.name)

    # Skip if a document with this exact filename already exists
    if ClaimDocument.objects.filter(file__endswith=file.name).exists():
        return JsonResponse({'skipped': True, 'reason': 'File already exists'})

    doc = ClaimDocument.objects.create(
        title=title,
        file=file,
        customer_name=customer_name,
        group_key=group_key,
    )

    return JsonResponse({
        'success': True,
        'document': {
            'id': doc.id,
            'title': doc.title,
            'filename': doc.filename,
            'group_key': doc.group_key,
        }
    })
