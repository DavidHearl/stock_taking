from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.contrib import messages
import io
import zipfile
import logging
from pathlib import Path
from datetime import datetime

# Configure logging
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)

# Database paths
BASE_DIR = Path(__file__).resolve().parent.parent
PRODUCTS_DB_PATH = BASE_DIR / 'order_generator_files' / 'src' / 'products.db'

# Storage path for CAD DB in DigitalOcean Spaces (same as cad_views.py)
CAD_DB_STORAGE_PATH = 'cad_data/cad_data.db'

# Global variables to cache imports
_board_logic = None
_workguru_logic = None


def get_board_logic():
    """Lazy import of board_logic module"""
    global _board_logic
    if _board_logic is None:
        try:
            from . import board_logic
            _board_logic = board_logic
            logger.info("Successfully imported board_logic module")
        except ImportError as e:
            logger.error(f"Failed to import board_logic module: {e}")
            _board_logic = False
    return _board_logic if _board_logic is not False else None


def get_workguru_logic():
    """Lazy import of workguru_logic module"""
    global _workguru_logic
    if _workguru_logic is None:
        try:
            from . import workguru_logic
            _workguru_logic = workguru_logic
            logger.info("Successfully imported workguru_logic module")
        except ImportError as e:
            logger.error(f"Failed to import workguru_logic module: {e}")
            _workguru_logic = False
    return _workguru_logic if _workguru_logic is not False else None


@login_required
def generate_materials(request):
    """Main page for generating PNX and CSV files"""
    logger.info(f"User {request.user.username} accessed generate_materials page")
    
    # Get database stats
    db_stats = {
        'database_exists': False,
        'database_path': str(DATABASE_PATH),
        'products_db_exists': False,
        'products_db_path': str(PRODUCTS_DB_PATH),
    }
    
    if default_storage.exists(CAD_DB_STORAGE_PATH):
        # Always use DigitalOcean Spaces version
        try:
            db_stats['database_exists'] = True
            size = default_storage.size(CAD_DB_STORAGE_PATH)
            db_stats['database_size'] = f"{size / (1024*1024):.2f} MB"
            modified = default_storage.get_modified_time(CAD_DB_STORAGE_PATH)
            if modified:
                db_stats['database_last_modified'] = modified.strftime('%d %b %Y, %H:%M')
            logger.info(f"CAD database found in cloud storage")
        except Exception as e:
            logger.warning(f"Error reading CAD DB from storage: {e}")
    else:
        logger.warning(f"CAD database not found in cloud storage")
    
    if PRODUCTS_DB_PATH.exists():
        db_stats['products_db_exists'] = True
        db_stats['products_db_size'] = f"{PRODUCTS_DB_PATH.stat().st_size / (1024*1024):.2f} MB"
        mtime = datetime.fromtimestamp(PRODUCTS_DB_PATH.stat().st_mtime)
        db_stats['products_db_last_modified'] = mtime.strftime('%d %b %Y, %H:%M')
        logger.info(f"Products database found at {PRODUCTS_DB_PATH}")
    else:
        logger.warning(f"Products database not found at {PRODUCTS_DB_PATH}")
    
    # Check if modules can be loaded
    board_logic = get_board_logic()
    workguru_logic = get_workguru_logic()
    
    context = {
        'page_title': 'Order Generator',
        'db_stats': db_stats,
        'modules_loaded': board_logic is not None and workguru_logic is not None,
    }
    
    return render(request, 'material_generator/generate.html', context)


@login_required
def generate_pnx(request):
    """Generate PNX file for board orders"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST method required'}, status=400)
    
    logger.info(f"User {request.user.username} initiated PNX generation")
    
    # Get form data
    po_label = request.POST.get('po_label', '').strip()
    system_numbers = request.POST.get('system_numbers', '').strip()
    
    logger.info(f"PNX Generation - PO Label: {po_label}")
    logger.info(f"PNX Generation - System Numbers: {system_numbers}")
    
    # Validation
    if not po_label:
        logger.error("PNX Generation failed: PO Label is required")
        messages.error(request, 'PO Label is required')
        return JsonResponse({'error': 'PO Label is required'}, status=400)
    
    if not system_numbers:
        logger.error("PNX Generation failed: System Numbers are required")
        messages.error(request, 'System Numbers are required')
        return JsonResponse({'error': 'System Numbers are required'}, status=400)
    
    # Check if database exists
    if not DATABASE_PATH.exists():
        logger.error(f"PNX Generation failed: Database not found at {DATABASE_PATH}")
        messages.error(request, 'CAD database not found. Please sync the database first.')
        return JsonResponse({'error': 'Database not found'}, status=500)
    
    # Get the board_logic module
    board_logic = get_board_logic()
    if board_logic is None:
        logger.error("PNX Generation failed: board_logic module not loaded")
        messages.error(request, 'Board logic module not loaded')
        return JsonResponse({'error': 'Module not loaded'}, status=500)
    
    try:
        logger.info("Calling generate_board_order_file function")
        pnx_content = board_logic.generate_board_order_file(system_numbers, str(DATABASE_PATH))
        logger.info(f"PNX file generated successfully, size: {len(pnx_content)} bytes")
        
        # Prepare the file for download
        response = HttpResponse(pnx_content, content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="Boards_Order_{po_label}.pnx"'
        
        logger.info(f"PNX file download initiated for PO {po_label}")
        messages.success(request, f'PNX file generated successfully for PO {po_label}')
        
        return response
        
    except Exception as e:
        logger.exception(f"Error generating PNX file: {e}")
        messages.error(request, f'Error generating PNX file: {str(e)}')
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def generate_csv(request):
    """Generate WorkGuru CSV files"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST method required'}, status=400)
    
    logger.info(f"User {request.user.username} initiated CSV generation")
    
    # Get form data
    system_numbers_str = request.POST.get('system_numbers', '').strip()
    
    logger.info(f"CSV Generation - System Numbers: {system_numbers_str}")
    
    # Validation
    if not system_numbers_str:
        logger.error("CSV Generation failed: System Numbers are required")
        messages.error(request, 'System Numbers are required')
        return JsonResponse({'error': 'System Numbers are required'}, status=400)
    
    # Check if databases exist
    if not DATABASE_PATH.exists():
        logger.error(f"CSV Generation failed: CAD database not found at {DATABASE_PATH}")
        messages.error(request, 'CAD database not found. Please sync the database first.')
        return JsonResponse({'error': 'CAD database not found'}, status=500)
    
    if not PRODUCTS_DB_PATH.exists():
        logger.error(f"CSV Generation failed: Products database not found at {PRODUCTS_DB_PATH}")
        messages.error(request, 'Products database not found')
        return JsonResponse({'error': 'Products database not found'}, status=500)
    
    # Get the workguru_logic module
    workguru_logic = get_workguru_logic()
    if workguru_logic is None:
        logger.error("CSV Generation failed: workguru_logic module not loaded")
        messages.error(request, 'WorkGuru logic module not loaded')
        return JsonResponse({'error': 'Module not loaded'}, status=500)
    
    try:
        # Parse system numbers
        numList = [int(n.strip()) for n in system_numbers_str.splitlines() if n.strip()]
        logger.info(f"Parsed {len(numList)} system numbers: {numList}")
        
        if len(numList) == 0:
            logger.error("CSV Generation failed: No valid system numbers provided")
            messages.error(request, 'No valid system numbers provided')
            return JsonResponse({'error': 'No valid system numbers'}, status=400)
        
        if len(numList) == 1:
            # Single file generation
            sysNum = numList[0]
            logger.info(f"Generating single CSV for system number: {sysNum}")
            
            csv_content = workguru_logic.generate_workguru_csv(sysNum, str(DATABASE_PATH), str(PRODUCTS_DB_PATH))
            logger.info(f"CSV generated successfully for {sysNum}, size: {len(csv_content)} bytes")
            
            response = HttpResponse(csv_content, content_type='text/csv')
            response['Content-Disposition'] = f'attachment; filename="WG_Products_Import_{sysNum}.csv"'
            
            logger.info(f"Single CSV file download initiated for system {sysNum}")
            messages.success(request, f'CSV file generated successfully for system {sysNum}')
            
            return response
        
        else:
            # Multiple files - create zip
            logger.info(f"Generating zip archive for {len(numList)} CSV files")
            memory_file = io.BytesIO()
            
            with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
                for sysNum in numList:
                    logger.info(f"Generating CSV for system number: {sysNum}")
                    csv_content = workguru_logic.generate_workguru_csv(sysNum, str(DATABASE_PATH), str(PRODUCTS_DB_PATH))
                    file_name = f"WG_Products_Import_{sysNum}.csv"
                    zf.writestr(file_name, csv_content)
                    logger.info(f"Added {file_name} to zip archive, size: {len(csv_content)} bytes")
            
            memory_file.seek(0)
            
            response = HttpResponse(memory_file.getvalue(), content_type='application/zip')
            response['Content-Disposition'] = 'attachment; filename="WorkGuru_Imports.zip"'
            
            logger.info(f"Zip archive created successfully with {len(numList)} files")
            messages.success(request, f'ZIP file generated successfully with {len(numList)} CSV files')
            
            return response
        
    except ValueError as e:
        logger.error(f"CSV Generation failed: Invalid system number format - {e}")
        messages.error(request, 'Invalid system number format. Please enter numeric values only.')
        return JsonResponse({'error': 'Invalid system number format'}, status=400)
    
    except Exception as e:
        logger.exception(f"Error generating CSV file: {e}")
        messages.error(request, f'Error generating CSV file: {str(e)}')
        return JsonResponse({'error': str(e)}, status=500)
