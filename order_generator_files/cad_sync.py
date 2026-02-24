# CAD Database Sync Script
# Exports data from the CURRENT server (sr-bft-w10-lp07\SQLEXPRESS), merges with
# the legacy database (one-time historical data), and pushes to Atlas.
#
# Run daily from: C:\Users\OlympusPrintServer\OneDrive - Sliderobes\Desktop\Process Checking
#
# This replaces the old 3-script workflow:
#   get_legacy_db.py  -> no longer needed (legacy server is offline)
#   get_current_db.py -> incorporated here (Step 1)
#   merge_db_and_push.py -> incorporated here (Steps 2 & 3)

import sqlalchemy
import urllib
import pandas as pd
import sqlite3
import os
import socket
import logging
import requests
from datetime import datetime

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

# --- Logging ---
log_filename = f'cad_sync_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_filename),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- SQL Server (Current CAD Server) ---
CURRENT_SERVER_NAME = 'sr-bft-w10-lp07'
INSTANCE_NAME = 'SQLEXPRESS1'

current_hostname = socket.gethostname().lower()
if current_hostname == CURRENT_SERVER_NAME.lower():
    CURRENT_SERVER = f'localhost\\{INSTANCE_NAME}'
else:
    CURRENT_SERVER = f'{CURRENT_SERVER_NAME}\\{INSTANCE_NAME}'

# Tables to export from MSSQL
TABLES_TO_EXPORT = {
    'TORDINE': '[ECADMASTER].[dbo].[TORDINE]',
    'CUSTOMERID': '[ECADMASTER].[dbo].[CUSTOMERID]',
    'DISTINTAT': '[ECADMASTER].[dbo].[DISTINTAT]'
}

# --- File Paths ---
DB_DIR = r'C:\Users\OlympusPrintServer\OneDrive - Sliderobes\Desktop\Process Checking'
CURRENT_DB = os.path.join(DB_DIR, 'cad_db_current.db')
LEGACY_DB = os.path.join(DB_DIR, 'cad_db_legacy.db')
MERGED_DB = os.path.join(DB_DIR, 'cad_data.db')

# Tables in the merged database
TABLES_TO_MERGE = ['TORDINE', 'CUSTOMERID', 'DISTINTAT', 'articoli']

# --- Atlas (DigitalOcean Django App) ---
ATLAS_URL = 'https://atlas-gxbq5.ondigitalocean.app/api/cad-db/upload/'
ATLAS_API_KEY = '7JKMkagyD38JmfnPqWy6WQ73vnBxE6zq'  # Must match CAD_DB_API_KEY env var on Atlas


# ═══════════════════════════════════════════════════════════════
# STEP 1: Export from Current SQL Server
# ═══════════════════════════════════════════════════════════════

def export_current_server():
    """Connect to the current SQL Server and export CAD tables to cad_db_current.db"""
    logger.info("=" * 60)
    logger.info("STEP 1: EXPORT FROM CURRENT SERVER")
    logger.info("=" * 60)
    logger.info(f"Target server: {CURRENT_SERVER}")
    logger.info(f"Connection type: {'LOCAL' if 'localhost' in CURRENT_SERVER else 'REMOTE'}")

    # Try multiple connection methods
    instance_names = ['SQLEXPRESS', 'SQLEXPRESS1']
    if 'localhost' in CURRENT_SERVER:
        server_prefixes = ['localhost', '.', '(local)']
    else:
        server_prefixes = [CURRENT_SERVER_NAME]

    successful_engine = None

    for prefix in server_prefixes:
        if successful_engine:
            break
        for instance in instance_names:
            try:
                test_server = f"{prefix}\\{instance}"
                test_conn_str = f"Driver={{SQL Server}};SERVER={test_server};Database=ECADMASTER;Trusted_Connection=yes;"

                logger.info(f"Trying: {test_server}")
                mssql_quoted = urllib.parse.quote_plus(test_conn_str)
                test_engine = sqlalchemy.create_engine(f'mssql+pyodbc:///?odbc_connect={mssql_quoted}')

                with test_engine.connect() as test_conn:
                    result = test_conn.execute(sqlalchemy.text("SELECT @@VERSION"))
                    version = result.fetchone()[0]
                    logger.info(f"[OK] Connected to {test_server}")
                    logger.info(f"SQL Server: {version[:80]}...")
                    successful_engine = test_engine
                    break

            except Exception as e:
                logger.warning(f"[FAILED] {test_server}")
                continue

    if not successful_engine:
        logger.error("[FATAL] Could not connect to SQL Server")
        return False

    # Export tables
    sqlite_engine = None
    try:
        os.makedirs(DB_DIR, exist_ok=True)

        if os.path.exists(CURRENT_DB):
            os.remove(CURRENT_DB)

        sqlite_engine = sqlalchemy.create_engine(f'sqlite:///{CURRENT_DB}')

        with successful_engine.connect() as cnxn:
            # Get recent job numbers (last year)
            logger.info("Fetching recent job numbers...")
            recent_jobs_query = "SELECT NUMERO FROM [ECADMASTER].[dbo].[TORDINE] WHERE DATA >= DATEADD(year, -1, GETDATE())"
            recent_jobs_df = pd.read_sql(recent_jobs_query, cnxn)

            if recent_jobs_df.empty:
                logger.warning("No recent jobs found")
                return False

            job_numbers_string = ','.join(map(str, recent_jobs_df['NUMERO'].tolist()))
            logger.info(f"[OK] Found {len(recent_jobs_df)} recent jobs")

            # Export filtered tables
            for sqlite_name, mssql_name in TABLES_TO_EXPORT.items():
                query = f"SELECT * FROM {mssql_name} WHERE NUMERO IN ({job_numbers_string})"
                df = pd.read_sql(query, cnxn)
                df.to_sql(sqlite_name, sqlite_engine, if_exists='replace', index=False)
                logger.info(f"[OK] {sqlite_name}: {len(df)} rows")

            # Export articoli (unfiltered)
            df_articoli = pd.read_sql('SELECT * FROM [sliderobes].[dbo].[articoli]', cnxn)
            df_articoli.to_sql('articoli', sqlite_engine, if_exists='replace', index=False)
            logger.info(f"[OK] articoli: {len(df_articoli)} rows")

        logger.info(f"[SUCCESS] Current DB: {os.path.getsize(CURRENT_DB) / 1024:.1f} KB")
        return True

    except Exception as e:
        logger.error(f"Export failed: {e}", exc_info=True)
        return False
    finally:
        if sqlite_engine:
            sqlite_engine.dispose()


# ═══════════════════════════════════════════════════════════════
# STEP 2: Merge with Legacy Database
# ═══════════════════════════════════════════════════════════════

def merge_databases():
    """Merge cad_db_legacy.db (historical) and cad_db_current.db into cad_data.db"""
    logger.info("")
    logger.info("=" * 60)
    logger.info("STEP 2: MERGE DATABASES")
    logger.info("=" * 60)

    # Current DB is required
    if not os.path.exists(CURRENT_DB):
        logger.error(f"Current database not found: {CURRENT_DB}")
        return False

    # Legacy DB is optional (historical data from old server)
    has_legacy = os.path.exists(LEGACY_DB)
    if has_legacy:
        logger.info(f"[OK] Legacy DB: {os.path.getsize(LEGACY_DB) / 1024 / 1024:.2f} MB")
    else:
        logger.info("[INFO] No legacy database found — using current data only")

    logger.info(f"[OK] Current DB: {os.path.getsize(CURRENT_DB) / 1024 / 1024:.2f} MB")

    # Remove old merged database
    if os.path.exists(MERGED_DB):
        os.remove(MERGED_DB)

    try:
        current_conn = sqlite3.connect(CURRENT_DB)
        legacy_conn = sqlite3.connect(LEGACY_DB) if has_legacy else None
        merged_conn = sqlite3.connect(MERGED_DB)

        for table_name in TABLES_TO_MERGE:
            logger.info(f"  Merging: {table_name}")

            # Read current
            try:
                df_current = pd.read_sql(f"SELECT * FROM {table_name}", current_conn)
            except Exception:
                df_current = pd.DataFrame()

            # Read legacy (if available)
            df_legacy = pd.DataFrame()
            if legacy_conn:
                try:
                    df_legacy = pd.read_sql(f"SELECT * FROM {table_name}", legacy_conn)
                except Exception:
                    pass

            # Merge
            if not df_legacy.empty and not df_current.empty:
                df_merged = pd.concat([df_legacy, df_current], ignore_index=True)
                df_merged = df_merged.drop_duplicates()
            elif not df_current.empty:
                df_merged = df_current
            elif not df_legacy.empty:
                df_merged = df_legacy
            else:
                logger.warning(f"    No data for {table_name}")
                continue

            df_merged.to_sql(table_name, merged_conn, if_exists='replace', index=False)
            logger.info(f"    [OK] {len(df_merged)} rows")

        current_conn.close()
        if legacy_conn:
            legacy_conn.close()
        merged_conn.close()

        logger.info(f"[SUCCESS] Merged DB: {os.path.getsize(MERGED_DB) / 1024 / 1024:.2f} MB")
        return True

    except Exception as e:
        logger.error(f"Merge failed: {e}", exc_info=True)
        return False


# ═══════════════════════════════════════════════════════════════
# STEP 3: Push to Atlas
# ═══════════════════════════════════════════════════════════════

def push_to_atlas():
    """Upload the merged cad_data.db to the Atlas Django app on DigitalOcean"""
    logger.info("")
    logger.info("=" * 60)
    logger.info("STEP 3: PUSH TO ATLAS")
    logger.info("=" * 60)

    if not os.path.exists(MERGED_DB):
        logger.error(f"Merged database not found: {MERGED_DB}")
        return False

    file_size = os.path.getsize(MERGED_DB)
    logger.info(f"Uploading cad_data.db ({file_size / 1024:.1f} KB) to {ATLAS_URL}")

    try:
        with open(MERGED_DB, 'rb') as db_file:
            files = {'database': ('cad_data.db', db_file, 'application/octet-stream')}
            headers = {'X-API-Key': ATLAS_API_KEY}

            response = requests.post(ATLAS_URL, files=files, headers=headers, timeout=300)

        if response.status_code == 200:
            logger.info(f"[SUCCESS] Uploaded to Atlas")
            logger.info(f"Response: {response.json()}")
            return True
        else:
            logger.error(f"[FAILED] HTTP {response.status_code}: {response.text}")
            return False

    except Exception as e:
        logger.error(f"Upload failed: {e}", exc_info=True)
        return False


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("CAD DATABASE SYNC")
    logger.info(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Host: {socket.gethostname()}")
    logger.info("=" * 60)

    success = True

    try:
        # Step 1: Export from current server
        if not export_current_server():
            logger.error("[FAILED] Step 1: Export failed")
            success = False
        else:
            # Step 2: Merge with legacy
            if not merge_databases():
                logger.error("[FAILED] Step 2: Merge failed")
                success = False
            else:
                # Step 3: Push to Atlas
                if not push_to_atlas():
                    logger.error("[FAILED] Step 3: Push failed")
                    success = False

        if success:
            logger.info("")
            logger.info("=" * 60)
            logger.info("[COMPLETE] CAD database synced to Atlas successfully")
            logger.info("=" * 60)
        else:
            logger.error("")
            logger.error("=" * 60)
            logger.error("[FAILED] Sync did not complete — check errors above")
            logger.error("=" * 60)

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)

    finally:
        logger.info(f"Log: {os.path.abspath(log_filename)}")
        print("\n" + "=" * 60)
        print("Press Enter to close...")
        print("=" * 60)
        input()
