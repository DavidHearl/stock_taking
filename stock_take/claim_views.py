from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.db.models import Q
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from .models import ClaimDocument
import os


@login_required
def claim_service(request):
    """Main claim service page with search and PDF listing."""
    query = request.GET.get('q', '').strip()

    documents = ClaimDocument.objects.all()
    if query:
        documents = documents.filter(
            Q(title__icontains=query) |
            Q(customer_name__icontains=query) |
            Q(file__icontains=query)
        )

    context = {
        'documents': documents,
        'search_query': query,
        'total_count': ClaimDocument.objects.count(),
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
        # Default title from filename
        title = os.path.splitext(file.name)[0]

    doc = ClaimDocument.objects.create(
        title=title,
        file=file,
        customer_name=customer_name,
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


@csrf_exempt
def claim_api_upload(request):
    """API endpoint for automated PDF uploads from remote PC.
    Authenticates via X-API-Key header instead of session login.
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

    if not title:
        title = os.path.splitext(file.name)[0]

    # Skip if a document with this exact filename already exists
    if ClaimDocument.objects.filter(file__endswith=file.name).exists():
        return JsonResponse({'skipped': True, 'reason': 'File already exists'})

    doc = ClaimDocument.objects.create(
        title=title,
        file=file,
        customer_name=customer_name,
    )

    return JsonResponse({
        'success': True,
        'document': {
            'id': doc.id,
            'title': doc.title,
            'filename': doc.filename,
        }
    })
