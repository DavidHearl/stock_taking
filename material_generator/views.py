from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.contrib import messages
import io
import base64
import zipfile
import logging
import sqlite3
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


def _open_cad_db():
    """Read the CAD database from cloud storage and return an in-memory SQLite connection."""
    import tempfile, os
    db_bytes = default_storage.open(CAD_DB_STORAGE_PATH, 'rb').read()
    # Write to a temp file and open — avoids conn.deserialize() which
    # requires Python 3.11.4+ and a specific sqlite3 compile flag.
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
    try:
        tmp.write(db_bytes)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
    except Exception:
        os.unlink(tmp.name)
        raise
    # Stash the temp path so callers can clean up after closing
    conn._tmp_path = tmp.name
    return conn


def _close_cad_db(conn):
    """Close a CAD db connection and clean up its temp file."""
    import os
    tmp_path = getattr(conn, '_tmp_path', None)
    conn.close()
    if tmp_path:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

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


def _format_board_summary(summary):
    """Turn the board generation summary dict into user-friendly report lines."""
    lines = []
    for s in summary.get('systems', []):
        lines.append(
            f"System {s['system']}: {s['board_rows']} board row(s) generated "
            f"(components: {s['total_components']}, board components: {s['board_components']})."
        )
        for m in s.get('messages', []):
            lines.append(f"  Warning: {m}")
    lines.append(f"Total board rows generated: {summary.get('total_data_rows', 0)}")
    return lines


def _format_accessory_summary(systems):
    """Turn a list of per-system accessory summaries into report lines."""
    lines = []
    total = 0
    for s in systems:
        total += s.get('rows', 0)
        lines.append(
            f"System {s['system']}: {s.get('rows', 0)} accessory row(s) generated "
            f"(components: {s['total_components']}, accessories: {s['accessory_components']}, "
            f"glass: {s['glass_components']}, raumplus: {s['raumplus_components']})."
        )
        for m in s.get('messages', []):
            lines.append(f"  Warning: {m}")
    lines.append(f"Total accessory rows generated: {total}")
    return lines


def _wants_json(request):
    """Detect an AJAX/fetch request expecting a JSON response."""
    return (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'application/json' in request.headers.get('Accept', '')
    )


@login_required
def generate_materials(request):
    """Main page for generating PNX and CSV files"""
    logger.info(f"User {request.user.username} accessed generate_materials page")
    return render(request, 'material_generator/generate.html', _generate_context())


def _generate_context():
    """Build the context (DB stats + module status) for the generate page."""
    # Get database stats
    db_stats = {
        'database_exists': False,
        'database_path': CAD_DB_STORAGE_PATH,
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
    
    return {
        'page_title': 'Order Generator',
        'db_stats': db_stats,
        'modules_loaded': board_logic is not None and workguru_logic is not None,
    }


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
    
    # Check if database exists in cloud storage
    if not default_storage.exists(CAD_DB_STORAGE_PATH):
        logger.error(f"PNX Generation failed: CAD database not found in cloud storage")
        messages.error(request, 'CAD database not found. Please sync the database first.')
        return JsonResponse({'error': 'Database not found'}, status=500)
    
    # Get the board_logic module
    board_logic = get_board_logic()
    if board_logic is None:
        logger.error("PNX Generation failed: board_logic module not loaded")
        messages.error(request, 'Board logic module not loaded')
        return JsonResponse({'error': 'Module not loaded'}, status=500)
    
    try:
        # Open CAD database from cloud storage into memory
        conn = _open_cad_db()
        logger.info("Calling generate_board_order_file function")
        summary = {}
        pnx_content = board_logic.generate_board_order_file(system_numbers, conn, summary=summary)
        _close_cad_db(conn)

        report_lines = _format_board_summary(summary)
        for line in report_lines:
            logger.info(f"PNX summary: {line}")
        total_rows = summary.get('total_data_rows', 0)
        logger.info(f"PNX file generated, {total_rows} data row(s), size: {len(pnx_content)} bytes")

        # Nothing was generated - tell the user WHY instead of returning an empty file
        if total_rows == 0:
            logger.warning(f"PNX Generation produced 0 rows for PO {po_label}")
            if _wants_json(request):
                return JsonResponse({
                    'success': False,
                    'no_data': True,
                    'message': 'No board rows were generated. See the details below.',
                    'summary': report_lines,
                })
            messages.warning(request, 'No board rows were generated. ' + ' '.join(report_lines))
            return render(request, 'material_generator/generate.html', _generate_context())

        filename = f'Boards_Order_{po_label}.pnx'

        if _wants_json(request):
            return JsonResponse({
                'success': True,
                'filename': filename,
                'content_type': 'text/csv',
                'file_b64': base64.b64encode(pnx_content.encode('utf-8')).decode('ascii'),
                'summary': report_lines,
            })

        # Fallback (non-AJAX) - direct file download
        response = HttpResponse(pnx_content, content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        logger.info(f"PNX file download initiated for PO {po_label}")
        messages.success(request, f'PNX file generated successfully for PO {po_label}')
        return response

    except Exception as e:
        logger.exception(f"Error generating PNX file: {e}")
        if _wants_json(request):
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
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
    if not default_storage.exists(CAD_DB_STORAGE_PATH):
        logger.error(f"CSV Generation failed: CAD database not found in cloud storage")
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
        # Open CAD database from cloud storage into memory
        conn = _open_cad_db()

        # Parse system numbers
        numList = [int(n.strip()) for n in system_numbers_str.splitlines() if n.strip()]
        logger.info(f"Parsed {len(numList)} system numbers: {numList}")
        
        if len(numList) == 0:
            logger.error("CSV Generation failed: No valid system numbers provided")
            _close_cad_db(conn)
            if _wants_json(request):
                return JsonResponse({'success': False, 'error': 'No valid system numbers'}, status=400)
            messages.error(request, 'No valid system numbers provided')
            return JsonResponse({'error': 'No valid system numbers'}, status=400)

        # Generate a CSV for every system number, collecting per-system diagnostics
        per_system = {}
        system_summaries = []
        total_rows = 0
        for sysNum in numList:
            logger.info(f"Generating CSV for system number: {sysNum}")
            sys_summary = {}
            csv_content = workguru_logic.generate_workguru_csv(
                sysNum, conn, str(PRODUCTS_DB_PATH), summary=sys_summary
            )
            per_system[sysNum] = csv_content
            info = sys_summary.get('system', {'system': sysNum, 'rows': 0, 'total_components': 0,
                                              'accessory_components': 0, 'glass_components': 0,
                                              'raumplus_components': 0, 'messages': []})
            system_summaries.append(info)
            total_rows += info.get('rows', 0)
            logger.info(f"System {sysNum}: {info.get('rows', 0)} row(s), size: {len(csv_content)} bytes")

        _close_cad_db(conn)

        report_lines = _format_accessory_summary(system_summaries)
        for line in report_lines:
            logger.info(f"CSV summary: {line}")

        # Nothing was generated for ANY system - explain why instead of an empty file
        if total_rows == 0:
            logger.warning("CSV Generation produced 0 rows for all systems")
            if _wants_json(request):
                return JsonResponse({
                    'success': False,
                    'no_data': True,
                    'message': 'No accessory rows were generated. See the details below.',
                    'summary': report_lines,
                })
            messages.warning(request, 'No accessory rows were generated. ' + ' '.join(report_lines))
            return render(request, 'material_generator/generate.html', _generate_context())

        # Build the download payload (single CSV or a ZIP for multiple systems)
        if len(numList) == 1:
            sysNum = numList[0]
            filename = f"WG_Products_Import_{sysNum}.csv"
            file_bytes = per_system[sysNum].encode('utf-8')
            content_type = 'text/csv'
        else:
            memory_file = io.BytesIO()
            with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
                for sysNum in numList:
                    file_name = f"WG_Products_Import_{sysNum}.csv"
                    zf.writestr(file_name, per_system[sysNum])
                    logger.info(f"Added {file_name} to zip archive")
            memory_file.seek(0)
            file_bytes = memory_file.getvalue()
            filename = 'WorkGuru_Imports.zip'
            content_type = 'application/zip'

        if _wants_json(request):
            return JsonResponse({
                'success': True,
                'filename': filename,
                'content_type': content_type,
                'file_b64': base64.b64encode(file_bytes).decode('ascii'),
                'summary': report_lines,
            })

        # Fallback (non-AJAX) - direct file download
        response = HttpResponse(file_bytes, content_type=content_type)
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        messages.success(request, f'File generated successfully for {len(numList)} system(s)')
        return response

    except ValueError as e:
        logger.error(f"CSV Generation failed: Invalid system number format - {e}")
        if _wants_json(request):
            return JsonResponse({
                'success': False,
                'error': 'Invalid system number format. Please enter numeric values only (one per line).'
            }, status=400)
        messages.error(request, 'Invalid system number format. Please enter numeric values only.')
        return JsonResponse({'error': 'Invalid system number format'}, status=400)
    
    except Exception as e:
        logger.exception(f"Error generating CSV file: {e}")
        if _wants_json(request):
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
        messages.error(request, f'Error generating CSV file: {str(e)}')
        return JsonResponse({'error': str(e)}, status=500)
