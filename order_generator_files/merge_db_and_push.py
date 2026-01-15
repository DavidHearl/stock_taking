# Database Merge and Push Script
# Merges cad_db_legacy.db and cad_db_current.db into cad_data.db and pushes to order generator
# Run this from C:\Users\OlympusPrintServer\OneDrive - Sliderobes\Desktop\Process Checking
# after both get_legacy_db.py and get_current_db.py have been executed

import sqlite3
import pandas as pd
import os
import logging
import requests
from datetime import datetime

# --- Logging Configuration ---
log_filename = f'merge_db_and_push_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_filename),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- Configuration ---
DB_DIR = r'C:\Users\OlympusPrintServer\OneDrive - Sliderobes\Desktop\Process Checking'
LEGACY_DB = os.path.join(DB_DIR, 'cad_db_legacy.db')
CURRENT_DB = os.path.join(DB_DIR, 'cad_db_current.db')
MERGED_DB = os.path.join(DB_DIR, 'cad_data.db')

# Tables to merge
TABLES_TO_MERGE = ['TORDINE', 'CUSTOMERID', 'DISTINTAT', 'articoli']

# Flask app configuration (from sync_script.py)
FLASK_APP_URL = 'https://sliderobesit.pythonanywhere.com/dbupdate'
API_SECRET_KEY = 'a-very-strong-and-secret-password'

logger.info("=" * 60)
logger.info("DATABASE MERGE AND PUSH SCRIPT")
logger.info("=" * 60)
logger.info(f"Database directory: {DB_DIR}")
logger.info(f"Legacy database: {LEGACY_DB}")
logger.info(f"Current database: {CURRENT_DB}")
logger.info(f"Output database: {MERGED_DB}")
logger.info(f"Upload URL: {FLASK_APP_URL}")
logger.info("=" * 60)


def check_files_exist():
    """Verify that both source databases exist and show their contents"""
    logger.info("Checking for source database files...")
    
    if not os.path.exists(LEGACY_DB):
        logger.error(f"Legacy database not found: {LEGACY_DB}")
        return False
    else:
        logger.info(f"[OK] Legacy database found: {os.path.getsize(LEGACY_DB) / 1024 / 1024:.2f} MB")
        
        # Check what's in the legacy database
        try:
            conn = sqlite3.connect(LEGACY_DB)
            for table in TABLES_TO_MERGE:
                try:
                    df = pd.read_sql(f"SELECT COUNT(*) as count FROM {table}", conn)
                    count = df['count'].iloc[0]
                    logger.info(f"  - {table}: {count:,} rows")
                except:
                    logger.warning(f"  - {table}: Could not read")
            conn.close()
        except Exception as e:
            logger.warning(f"Could not inspect legacy database: {e}")
    
    if not os.path.exists(CURRENT_DB):
        logger.error(f"Current database not found: {CURRENT_DB}")
        return False
    else:
        logger.info(f"[OK] Current database found: {os.path.getsize(CURRENT_DB) / 1024 / 1024:.2f} MB")
        
        # Check what's in the current database
        try:
            conn = sqlite3.connect(CURRENT_DB)
            for table in TABLES_TO_MERGE:
                try:
                    df = pd.read_sql(f"SELECT COUNT(*) as count FROM {table}", conn)
                    count = df['count'].iloc[0]
                    logger.info(f"  - {table}: {count:,} rows")
                except:
                    logger.warning(f"  - {table}: Could not read")
            conn.close()
        except Exception as e:
            logger.warning(f"Could not inspect current database: {e}")
    
    return True


def merge_databases():
    """Merges legacy and current databases into unified cad_data.db (preserves source files)"""
    logger.info("Starting database merge process...")
    
    # Check if merged database exists and warn user
    if os.path.exists(MERGED_DB):
        logger.warning(f"Merged database already exists: {MERGED_DB}")
        logger.warning("This will be OVERWRITTEN with the new merge")
        logger.info("Removing old merged database...")
        os.remove(MERGED_DB)
    
    try:
        # Connect to all databases
        logger.info("Connecting to databases...")
        logger.info("NOTE: Source databases (legacy and current) will be PRESERVED")
        legacy_conn = sqlite3.connect(LEGACY_DB)
        current_conn = sqlite3.connect(CURRENT_DB)
        merged_conn = sqlite3.connect(MERGED_DB)
        
        logger.info("[OK] All database connections established")
        
        # Merge each table
        for table_name in TABLES_TO_MERGE:
            logger.info(f"\nProcessing table: {table_name}")
            logger.info("-" * 60)
            
            # Read from legacy database
            try:
                logger.info(f"Reading {table_name} from legacy database...")
                df_legacy = pd.read_sql(f"SELECT * FROM {table_name}", legacy_conn)
                logger.info(f"[OK] Legacy: {len(df_legacy)} rows")
            except Exception as e:
                logger.warning(f"Could not read {table_name} from legacy: {e}")
                df_legacy = pd.DataFrame()
            
            # Read from current database
            try:
                logger.info(f"Reading {table_name} from current database...")
                df_current = pd.read_sql(f"SELECT * FROM {table_name}", current_conn)
                logger.info(f"[OK] Current: {len(df_current)} rows")
            except Exception as e:
                logger.warning(f"Could not read {table_name} from current: {e}")
                df_current = pd.DataFrame()
            
            # Merge the dataframes
            if not df_legacy.empty and not df_current.empty:
                logger.info("Merging data...")
                
                # Concatenate both dataframes
                df_merged = pd.concat([df_legacy, df_current], ignore_index=True)
                before_dedup = len(df_merged)
                
                # Different merge strategy based on table type
                if table_name == 'DISTINTAT':
                    # DISTINTAT: Detail/line items table - keep ALL rows, only remove exact duplicates
                    # This table has multiple rows per NUMERO (job details)
                    df_merged = df_merged.drop_duplicates()  # Remove only exact duplicate rows
                    after_dedup = len(df_merged)
                    duplicates_removed = before_dedup - after_dedup
                    logger.info(f"[OK] Merged: {len(df_merged)} rows (removed {duplicates_removed} exact duplicates, preserved all job details)")
                    
                else:
                    # For all other tables (TORDINE, CUSTOMERID, articoli):
                    # Remove exact duplicates only
                    df_merged = df_merged.drop_duplicates()
                    after_dedup = len(df_merged)
                    duplicates_removed = before_dedup - after_dedup
                    logger.info(f"[OK] Merged: {len(df_merged)} rows (removed {duplicates_removed} exact duplicates)")
                
            elif not df_legacy.empty:
                logger.info("Only legacy data available")
                df_merged = df_legacy
            elif not df_current.empty:
                logger.info("Only current data available")
                df_merged = df_current
            else:
                logger.warning(f"No data found in either database for {table_name}")
                continue
            
            # Write to merged database
            logger.info(f"Writing {len(df_merged)} rows to merged database...")
            df_merged.to_sql(table_name, merged_conn, if_exists='replace', index=False)
            logger.info(f"[SUCCESS] {table_name} merged successfully")
        
        # Close connections
        legacy_conn.close()
        current_conn.close()
        merged_conn.close()
        
        logger.info("=" * 60)
        logger.info("[SUCCESS] Database merge completed")
        logger.info(f"Output file: {MERGED_DB}")
        logger.info(f"File size: {os.path.getsize(MERGED_DB) / 1024 / 1024:.2f} MB")
        logger.info("=" * 60)
        logger.info("NOTE: Source databases preserved:")
        logger.info(f"  - Legacy: {LEGACY_DB} ({os.path.getsize(LEGACY_DB) / 1024 / 1024:.2f} MB)")
        logger.info(f"  - Current: {CURRENT_DB} ({os.path.getsize(CURRENT_DB) / 1024 / 1024:.2f} MB)")
        logger.info("=" * 60)
        
        # Display summary
        merged_conn_read = sqlite3.connect(MERGED_DB)
        logger.info("\nMERGED DATABASE SUMMARY:")
        logger.info("-" * 60)
        for table_name in TABLES_TO_MERGE:
            try:
                df = pd.read_sql(f"SELECT COUNT(*) as count FROM {table_name}", merged_conn_read)
                count = df['count'].iloc[0]
                logger.info(f"{table_name}: {count} rows")
            except:
                logger.warning(f"{table_name}: Could not read count")
        merged_conn_read.close()
        logger.info("=" * 60)
        
        return True
        
    except Exception as e:
        logger.error(f"Error during merge: {e}", exc_info=True)
        return False


def send_db_to_flask_app():
    """Sends the merged SQLite file to the Flask application's update endpoint."""
    logger.info("\n" + "=" * 60)
    logger.info("UPLOADING DATABASE TO ORDER GENERATOR")
    logger.info("=" * 60)
    
    if not os.path.exists(MERGED_DB):
        logger.error(f"Error: {MERGED_DB} not found. Cannot send to server.")
        return False

    logger.info(f"Sending cad_data.db to Flask app at {FLASK_APP_URL}...")
    logger.info(f"File size: {os.path.getsize(MERGED_DB) / 1024:.2f} KB")
    
    try:
        with open(MERGED_DB, 'rb') as db_file:
            # Prepare the file and headers for the POST request
            files = {'database': (os.path.basename(MERGED_DB), db_file, 'application/octet-stream')}
            headers = {'X-API-Key': API_SECRET_KEY}
            
            # Make the request
            logger.info("Uploading to server...")
            response = requests.post(FLASK_APP_URL, files=files, headers=headers, timeout=300)
        
        # Check the response
        if response.status_code == 200:
            logger.info("[SUCCESS] Successfully uploaded database to the server.")
            logger.info(f"Server response: {response.json()}")
            return True
        else:
            logger.error(f"[FAILED] Error uploading file. Server responded with status code {response.status_code}.")
            logger.error(f"Server response: {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"[FAILED] An error occurred while sending the file: {e}", exc_info=True)
        return False


if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("STARTING DATABASE MERGE AND PUSH")
    logger.info("=" * 60)
    logger.info(f"Log file: {log_filename}")
    
    try:
        # Step 1: Check files exist
        if not check_files_exist():
            logger.error("[FAILED] Source database files not found")
            logger.error("Please run get_legacy_db.py and get_current_db.py first")
        else:
            # Step 2: Merge databases
            if merge_databases():
                logger.info("\n[SUCCESS] Merge process completed successfully")
                
                # Step 3: Push to server
                if send_db_to_flask_app():
                    logger.info("\n[SUCCESS] Database uploaded to order generator successfully")
                else:
                    logger.error("\n[FAILED] Database upload failed")
            else:
                logger.error("[FAILED] Merge process failed")
    
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        logger.info(f"\nLog file saved to: {os.path.abspath(log_filename)}")
        print("\n" + "=" * 60)
        print("Press Enter to close this window...")
        print("=" * 60)
        input()
