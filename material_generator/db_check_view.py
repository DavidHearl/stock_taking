from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.contrib import messages
import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Database paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = BASE_DIR / 'cad_data.db'


@login_required
def check_database(request):
    """Diagnostic view to check database contents"""
    
    db_info = {
        'database_path': str(DATABASE_PATH),
        'database_exists': DATABASE_PATH.exists(),
        'tables': [],
        'sample_data': {},
    }
    
    if not DATABASE_PATH.exists():
        return render(request, 'material_generator/db_check.html', {'db_info': db_info})
    
    try:
        with sqlite3.connect(DATABASE_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Get list of tables
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            tables = cursor.fetchall()
            db_info['tables'] = [t[0] for t in tables]
            
            # Get row counts and sample data from key tables
            for table_name in ['TORDINE', 'CUSTOMERID', 'DISTINTAT', 'articoli']:
                if table_name in db_info['tables']:
                    # Get row count
                    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                    count = cursor.fetchone()[0]
                    
                    # Get column info
                    cursor.execute(f"PRAGMA table_info({table_name})")
                    columns = [row[1] for row in cursor.fetchall()]
                    
                    # Get sample rows
                    cursor.execute(f"SELECT * FROM {table_name} LIMIT 5")
                    samples = []
                    for row in cursor.fetchall():
                        samples.append(dict(row))
                    
                    # Get distinct NUMERO values if column exists
                    distinct_numeros = []
                    if 'NUMERO' in columns or 'numero' in columns:
                        try:
                            cursor.execute(f"SELECT DISTINCT numero FROM {table_name} ORDER BY numero DESC LIMIT 10")
                            distinct_numeros = [row[0] for row in cursor.fetchall()]
                        except:
                            pass
                    
                    db_info['sample_data'][table_name] = {
                        'count': count,
                        'columns': columns,
                        'samples': samples,
                        'distinct_numeros': distinct_numeros,
                    }
            
            logger.info(f"Database check completed: {len(db_info['tables'])} tables found")
            
    except Exception as e:
        logger.error(f"Error checking database: {e}", exc_info=True)
        db_info['error'] = str(e)
    
    return render(request, 'material_generator/db_check.html', {'db_info': db_info})
