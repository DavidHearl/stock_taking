# Legacy Server Database Export Script
# Exports data from the OLD server (sr-ltp-tmcgrath\SQLEXPRESS1) to cad_db_legacy.db
# Run this from C:\Users\OlympusPrintServer\OneDrive - Sliderobes\Desktop\Process Checking

import sqlalchemy
import urllib
import pandas as pd
import os
import socket
import logging
from datetime import datetime

# --- Logging Configuration ---
log_filename = f'get_legacy_db_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_filename),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- Configuration for LEGACY Server ---
# Force connection to the OLD server (tmcgrath)
LEGACY_SERVER_NAME = 'sr-ltp-tmcgrath'  # OLD server hostname
INSTANCE_NAME = 'SQLEXPRESS1'

# Check if running locally - if so, use localhost for better connectivity
current_hostname = socket.gethostname().lower()
if current_hostname == LEGACY_SERVER_NAME.lower():
    # Running on the legacy server - use local connection
    LEGACY_SERVER = f'localhost\\{INSTANCE_NAME}'
else:
    # Running remotely - use full server name
    LEGACY_SERVER = f'{LEGACY_SERVER_NAME}\\{INSTANCE_NAME}'

MSSQL_CONN_STR = f"Driver={{SQL Server}};SERVER={LEGACY_SERVER};Database=ECADMASTER;Trusted_Connection=yes;"

# Tables to export
TABLES_TO_EXPORT = {
    'TORDINE': '[ECADMASTER].[dbo].[TORDINE]',
    'CUSTOMERID': '[ECADMASTER].[dbo].[CUSTOMERID]',
    'DISTINTAT': '[ECADMASTER].[dbo].[DISTINTAT]'
}

# Output database - save in Process Checking folder
OUTPUT_DIR = r'C:\Users\OlympusPrintServer\OneDrive - Sliderobes\Desktop\Process Checking'
SQLITE_DB_PATH = os.path.join(OUTPUT_DIR, 'cad_db_legacy.db')
SQLITE_DB_NAME = 'cad_db_legacy.db'

logger.info("=" * 60)
logger.info("LEGACY SERVER DATABASE EXPORT")
logger.info("=" * 60)
logger.info(f"Output directory: {OUTPUT_DIR}")
logger.info(f"SQLite database path: {SQLITE_DB_PATH}")
logger.info(f"Current hostname: {socket.gethostname()}")
logger.info(f"Target server: {LEGACY_SERVER}")
logger.info(f"Connection type: {'LOCAL' if 'localhost' in LEGACY_SERVER else 'REMOTE'}")
logger.info("=" * 60)

def export_tables_to_sqlite(mssql_engine):
    """Exports tables from legacy server to cad_db_legacy.db"""
    logger.info("Starting export from legacy server...")
    sqlite_engine = None
    try:
        # Ensure output directory exists
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        
        # Create SQLite engine with full path
        logger.info("Creating SQLite engine...")
        sqlite_engine = sqlalchemy.create_engine(f'sqlite:///{SQLITE_DB_PATH}')
        
        # Remove old database if exists
        if os.path.exists(SQLITE_DB_PATH):
            logger.info(f"Removing old database file: {SQLITE_DB_PATH}")
            os.remove(SQLITE_DB_PATH)
        
        logger.info("Connecting to MSSQL...")
        with mssql_engine.connect() as cnxn:
            logger.info("[OK] Connected to legacy server")
            
            # --- STEP 1: Get recent job numbers from last year ---
            logger.info("STEP 1: Fetching recent job numbers from last year...")
            recent_jobs_query = "SELECT NUMERO FROM [ECADMASTER].[dbo].[TORDINE] WHERE DATA >= DATEADD(year, -1, GETDATE())"
            recent_jobs_df = pd.read_sql(recent_jobs_query, cnxn)
            
            if recent_jobs_df.empty:
                logger.warning("No recent jobs found on legacy server.")
                return False
            
            recent_job_numbers = tuple(recent_jobs_df['NUMERO'].tolist())
            logger.info(f"[OK] Found {len(recent_job_numbers)} recent jobs on legacy server")
            
            # --- STEP 2: Convert job numbers to query string ---
            job_numbers_string = ','.join(map(str, recent_job_numbers))
            logger.info("STEP 2: Job numbers converted to query string")
            
            # --- STEP 3: Export filtered tables ---
            logger.info("STEP 3: Exporting filtered tables from legacy server...")
            for sqlite_table_name, mssql_full_name in TABLES_TO_EXPORT.items():
                logger.info(f"Exporting: {mssql_full_name} -> {sqlite_table_name}")
                
                query = f"SELECT * FROM {mssql_full_name} WHERE NUMERO IN ({job_numbers_string})"
                df = pd.read_sql(query, cnxn)
                df.to_sql(sqlite_table_name, sqlite_engine, if_exists='replace', index=False)
                logger.info(f"[OK] Exported {len(df)} rows to '{sqlite_table_name}'")
            
            # --- STEP 4: Export articoli table (unfiltered) ---
            logger.info("STEP 4: Exporting articoli table...")
            df_articoli = pd.read_sql('SELECT * FROM [sliderobes].[dbo].[articoli]', cnxn)
            df_articoli.to_sql('articoli', sqlite_engine, if_exists='replace', index=False)
            logger.info(f"[OK] Exported {len(df_articoli)} rows to 'articoli'")
        
        logger.info("=" * 60)
        logger.info("[SUCCESS] Legacy database created successfully")
        logger.info(f"File path: {SQLITE_DB_PATH}")
        logger.info(f"File size: {os.path.getsize(SQLITE_DB_PATH) / 1024:.2f} KB")
        logger.info("=" * 60)
        return True
        
    except Exception as e:
        logger.error(f"Error during export: {e}", exc_info=True)
        return False
    finally:
        if sqlite_engine:
            sqlite_engine.dispose()

if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("STARTING LEGACY SERVER EXPORT")
    logger.info("=" * 60)
    logger.info(f"Log file: {log_filename}")
    
    # Try multiple connection methods
    instance_names = ['SQLEXPRESS1', 'SQLEXPRESS']
    
    # If running locally, try local connection methods
    if 'localhost' in LEGACY_SERVER:
        server_prefixes = ['localhost', '.', '(local)']
    else:
        server_prefixes = [LEGACY_SERVER_NAME]
    
    successful_engine = None
    successful_connection = None
    
    logger.info("Attempting to connect to SQL Server...")
    logger.info("-" * 60)
    
    for prefix in server_prefixes:
        if successful_engine:
            break
        for instance in instance_names:
            try:
                test_server = f"{prefix}\\{instance}"
                test_conn_str = f"Driver={{SQL Server}};SERVER={test_server};Database=ECADMASTER;Trusted_Connection=yes;"
                
                logger.info(f"Trying: {test_server}")
                mssql_quoted_conn_str = urllib.parse.quote_plus(test_conn_str)
                test_engine = sqlalchemy.create_engine(f'mssql+pyodbc:///?odbc_connect={mssql_quoted_conn_str}')
                
                # Test connection
                with test_engine.connect() as test_conn:
                    result = test_conn.execute(sqlalchemy.text("SELECT @@VERSION"))
                    version = result.fetchone()[0]
                    logger.info(f"[SUCCESS] Connected to {test_server}")
                    logger.info(f"SQL Server Version: {version[:100]}...")
                    successful_engine = test_engine
                    successful_connection = test_server
                    break
                    
            except Exception as conn_error:
                logger.warning(f"[FAILED] Could not connect to {test_server}")
                logger.debug(f"Error: {str(conn_error)[:100]}")
                continue
    
    logger.info("=" * 60)
    
    try:
        if successful_engine:
            logger.info(f"[FINAL] Using connection: {successful_connection}")
            # Run export
            if export_tables_to_sqlite(successful_engine):
                logger.info("[SUCCESS] Legacy database export completed")
            else:
                logger.error("[FAILED] Legacy database export failed")
        else:
            logger.error("[FATAL] Could not connect to SQL Server!")
            logger.error(f"Tried all combinations of: {server_prefixes} x {instance_names}")
            
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        logger.info(f"\nLog file saved to: {os.path.abspath(log_filename)}")
        print("\n" + "=" * 60)
        print("Press Enter to close this window...")
        print("=" * 60)
        input()
