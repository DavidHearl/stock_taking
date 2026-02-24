"""CAD Database API endpoints.

Provides upload/download endpoints for the CAD SQLite database (cad_data.db).
The sync script on the print server exports from SQL Server, merges legacy + current,
then pushes the merged SQLite file here via the upload endpoint.
"""
import os
import logging
from datetime import datetime

from django.conf import settings
from django.http import JsonResponse, FileResponse, Http404
from django.views.decorators.csrf import csrf_exempt
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)

CAD_DB_STORAGE_PATH = 'cad_data/cad_data.db'


@csrf_exempt
def cad_db_upload(request):
    """API endpoint to receive the merged CAD SQLite database from the print server.
    
    Authenticates via X-API-Key header. Stores the file to DigitalOcean Spaces
    so it persists across deployments.
    
    Usage from script:
        POST /api/cad-db/upload/
        Headers: X-API-Key: <key>
        Body: multipart/form-data with 'database' file field
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    # Authenticate
    api_key = request.headers.get('X-API-Key', '')
    expected_key = getattr(settings, 'CAD_DB_API_KEY', '')
    if not api_key or api_key != expected_key:
        return JsonResponse({'error': 'Invalid API key'}, status=403)

    # Get uploaded file
    db_file = request.FILES.get('database')
    if not db_file:
        return JsonResponse({'error': 'No database file provided'}, status=400)

    try:
        file_size = db_file.size

        # Delete old file if it exists
        if default_storage.exists(CAD_DB_STORAGE_PATH):
            default_storage.delete(CAD_DB_STORAGE_PATH)

        # Save new file to storage (DigitalOcean Spaces)
        saved_path = default_storage.save(CAD_DB_STORAGE_PATH, db_file)

        logger.info(f"CAD database uploaded: {saved_path} ({file_size / 1024:.1f} KB)")

        return JsonResponse({
            'status': 'success',
            'message': 'CAD database updated.',
            'size_kb': round(file_size / 1024, 1),
            'updated_at': datetime.now().isoformat(),
        })

    except Exception as e:
        logger.error(f"CAD database upload failed: {e}", exc_info=True)
        return JsonResponse({'error': f'Upload failed: {str(e)}'}, status=500)


@csrf_exempt
def cad_db_download(request):
    """API endpoint to download the current CAD SQLite database.
    
    Authenticates via X-API-Key header.
    
    Usage:
        GET /api/cad-db/download/
        Headers: X-API-Key: <key>
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'GET required'}, status=405)

    # Authenticate
    api_key = request.headers.get('X-API-Key', '')
    expected_key = getattr(settings, 'CAD_DB_API_KEY', '')
    if not api_key or api_key != expected_key:
        return JsonResponse({'error': 'Invalid API key'}, status=403)

    if not default_storage.exists(CAD_DB_STORAGE_PATH):
        return JsonResponse({'error': 'CAD database not found. Run sync first.'}, status=404)

    try:
        db_file = default_storage.open(CAD_DB_STORAGE_PATH, 'rb')
        response = FileResponse(db_file, content_type='application/octet-stream')
        response['Content-Disposition'] = 'attachment; filename="cad_data.db"'
        return response
    except Exception as e:
        logger.error(f"CAD database download failed: {e}", exc_info=True)
        return JsonResponse({'error': f'Download failed: {str(e)}'}, status=500)


@csrf_exempt
def cad_db_status(request):
    """API endpoint to check the status of the CAD database.
    
    Usage:
        GET /api/cad-db/status/
        Headers: X-API-Key: <key>
    """
    api_key = request.headers.get('X-API-Key', '')
    expected_key = getattr(settings, 'CAD_DB_API_KEY', '')
    if not api_key or api_key != expected_key:
        return JsonResponse({'error': 'Invalid API key'}, status=403)

    if not default_storage.exists(CAD_DB_STORAGE_PATH):
        return JsonResponse({
            'exists': False,
            'message': 'No CAD database uploaded yet.',
        })

    try:
        size = default_storage.size(CAD_DB_STORAGE_PATH)
        modified = default_storage.get_modified_time(CAD_DB_STORAGE_PATH)
        return JsonResponse({
            'exists': True,
            'size_kb': round(size / 1024, 1),
            'size_mb': round(size / 1024 / 1024, 2),
            'last_updated': modified.isoformat() if modified else None,
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
