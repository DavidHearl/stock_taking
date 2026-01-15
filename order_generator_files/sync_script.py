# import pyodbc
import sqlalchemy
import urllib
import pandas as pd
import requests
import os
import socket
import logging
from datetime import datetime

# --- Logging Configuration ---
log_filename = f'sync_script_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_filename),
        logging.StreamHandler()  # Also print to console
    ]
)
logger = logging.getLogger(__name__)

# --- Configuration ---
# Your local MS SQL Server connection string
MSSQL_CONN_STR = "Driver={SQL Server}; SERVER=" + str(socket.gethostname()) + "\\SQLEXPRESS1; Database=ECADMASTER; Trusted_Connection=yes;"
# MSSQL_CONN_STR = 'Driver={SQL Server};SERVER=SR-BF-W10-LP08\SQLEXPRESS1;Database=ECADMASTER2;Trusted_Connection=yes;'
# The tables you need to export. Use the format [database_name.schema_name.table_name]
# Note: pandas will use the final part as the table name in SQLite (e.g., 'TORDINE')
TABLES_TO_EXPORT = {
    'TORDINE': '[ECADMASTER].[dbo].[TORDINE]',
    'CUSTOMERID': '[ECADMASTER].[dbo].[CUSTOMERID]',
    'DISTINTAT': '[ECADMASTER].[dbo].[DISTINTAT]'
}

# This ensures cad_data.db is always created in the same folder as this script.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SQLITE_DB_PATH = os.path.join(SCRIPT_DIR, 'cad_data.db')
# The local name for the generated SQLite file
SQLITE_DB_NAME = 'cad_data.db'

logger.info("=" * 60)
logger.info("ENVIRONMENT INFORMATION")
logger.info("=" * 60)
logger.info(f"Script directory: {SCRIPT_DIR}")
logger.info(f"SQLite database path: {SQLITE_DB_PATH}")
logger.info(f"Hostname: {socket.gethostname()}")
logger.info(f"Current working directory: {os.getcwd()}")
logger.info(f"Script absolute path: {os.path.abspath(__file__)}")
logger.info(f"MSSQL Connection String: {MSSQL_CONN_STR}")
logger.info("=" * 60)

# --- Your Flask App's Info ---
# The URL for your Flask app's update endpoint
FLASK_APP_URL = 'https://sliderobesit.pythonanywhere.com/dbupdate' # Change to your cloud URL when deployed
# A secret key to make sure only this script can update the database
API_SECRET_KEY = 'a-very-strong-and-secret-password' # IMPORTANT: Change this!

def export_tables_to_sqlite(mssql_engine):
    """Connects to MS SQL, reads tables, and saves them to SQLite using an efficient, multi-step filter."""
    logger.info("Starting export_tables_to_sqlite function")
    sqlite_engine = None
    try:
        logger.info("Creating SQLite engine...")
        sqlite_engine = sqlalchemy.create_engine(f'sqlite:///{SQLITE_DB_NAME}')
        logger.info("SQLite engine created successfully")
        logger.info(f"SQLite DB will be created at: {os.path.abspath(SQLITE_DB_PATH)}")

        if os.path.exists(SQLITE_DB_PATH):
            logger.info(f"Removing old database file: {SQLITE_DB_PATH}")
            os.remove(SQLITE_DB_PATH)
        else:
            logger.info(f"No existing database file found at: {SQLITE_DB_PATH}")

        logger.info("=" * 60)
        logger.info("ATTEMPTING MSSQL CONNECTION")
        logger.info("=" * 60)
        logger.info(f"MSSQL Engine: {mssql_engine}")
        logger.info(f"MSSQL Engine URL: {mssql_engine.url}")
        
        with mssql_engine.connect() as cnxn:
            logger.info("[OK] Successfully connected to MSSQL database")
            logger.info("=" * 60)
            
            # --- STEP 1: Get the list of recent job numbers first ---
            logger.info("STEP 1: Fetching list of recent job numbers from the last year...")
            logger.info(f"Query: SELECT NUMERO FROM [ECADMASTER].[dbo].[TORDINE] WHERE DATA >= DATEADD(year, -1, GETDATE())")
            recent_jobs_query = "SELECT NUMERO FROM [ECADMASTER].[dbo].[TORDINE] WHERE DATA >= DATEADD(year, -1, GETDATE())"
            recent_jobs_df = pd.read_sql(recent_jobs_query, cnxn)
            
            if recent_jobs_df.empty:
                logger.warning("No recent jobs found. Aborting.")
                return False

            recent_job_numbers = tuple(recent_jobs_df['NUMERO'].tolist())
            logger.info(f"[OK] Found {len(recent_job_numbers)} recent jobs.")
            if len(recent_job_numbers) > 0:
                logger.info(f"Sample job numbers: {list(recent_job_numbers)[:5]}...")

            # --- STEP 2: Convert the job numbers to a string for direct formatting ---
            # Create a comma-separated string like "(16904, 16911, ...)"
            job_numbers_string = ','.join(map(str, recent_job_numbers))
            logger.info("STEP 2: Job numbers converted to query string")

            # --- STEP 3: Loop through tables and filter them using the formatted string ---
            logger.info("STEP 3: Exporting filtered tables...")
            for sqlite_table_name, mssql_full_name in TABLES_TO_EXPORT.items():
                logger.info(f"Exporting filtered table: {mssql_full_name} -> {sqlite_table_name}")
                
                # Format the list of numbers directly into the SQL query string
                query = f"SELECT * FROM {mssql_full_name} WHERE NUMERO IN ({job_numbers_string})"
                logger.debug(f"Query: {query[:200]}...")  # Log first 200 chars of query
                
                # Execute the query without the 'params' argument
                df = pd.read_sql(query, cnxn)
                
                df.to_sql(sqlite_table_name, sqlite_engine, if_exists='replace', index=False)
                logger.info(f"[OK] Successfully exported {len(df)} rows to table '{sqlite_table_name}'.")

            # --- STEP 4: Export the 'articoli' table (it's not filtered by job number) ---
            logger.info("STEP 4: Exporting articoli table (unfiltered)...")
            logger.info("Exporting table: [sliderobes].[dbo].[articoli] -> articoli")
            df_articoli = pd.read_sql('SELECT * FROM [sliderobes].[dbo].[articoli]', cnxn)
            df_articoli.to_sql('articoli', sqlite_engine, if_exists='replace', index=False)
            logger.info(f"[OK] Successfully exported {len(df_articoli)} rows to table 'articoli'.")

        logger.info("=" * 60)
        logger.info("DATABASE FILE CREATION")
        logger.info("=" * 60)
        logger.info(f"[OK] Database file created at: {SQLITE_DB_PATH}")
        logger.info(f"[OK] Database file size: {os.path.getsize(SQLITE_DB_PATH) / 1024:.2f} KB")
        logger.info(f"[OK] Database file exists: {os.path.exists(SQLITE_DB_PATH)}")
        logger.info("=" * 60)
        return True
    except Exception as e:
        logger.error(f"An error occurred during export: {e}", exc_info=True)
        return False
    finally:
        if sqlite_engine:
            sqlite_engine.dispose()
            logger.info("SQLite engine disposed")


def send_db_to_flask_app():
    """Sends the generated SQLite file to the Flask application's update endpoint."""
    logger.info("Starting send_db_to_flask_app function")
    
    if not os.path.exists(SQLITE_DB_PATH):
        logger.error(f"Error: {SQLITE_DB_PATH} not found. Cannot send to server.")
        return

    logger.info(f"Sending {SQLITE_DB_NAME} to Flask app at {FLASK_APP_URL}...")
    logger.info(f"File size: {os.path.getsize(SQLITE_DB_PATH) / 1024:.2f} KB")
    
    try:
        with open(SQLITE_DB_PATH, 'rb') as db_file:
            # Prepare the file and headers for the POST request
            files = {'database': (os.path.basename(SQLITE_DB_PATH), db_file, 'application/octet-stream')}
            headers = {'X-API-Key': API_SECRET_KEY}
            
            # Make the request
            # response = requests.post(FLASK_APP_URL, files=files, headers=headers)
            response = requests.post(FLASK_APP_URL, files=files, headers=headers)
        
        # Check the response
        if response.status_code == 200:
            logger.info("Successfully uploaded database to the server.")
            logger.info(f"Server response: {response.json()}")
        else:
            logger.error(f"Error uploading file. Server responded with status code {response.status_code}.")
            logger.error(f"Server response: {response.text}")
            
    except Exception as e:
        logger.error(f"An error occurred while sending the file: {e}", exc_info=True)


def run_sync_and_upload(mssql_engine):
    """Exports the SQLite database and uploads it to the Flask app."""
    logger.info("=" * 60)
    logger.info("--- Starting Web Database Sync Process ---")
    logger.info("=" * 60)
    
    if export_tables_to_sqlite(mssql_engine):
        send_db_to_flask_app()
    else:
        logger.error("Export failed. Skipping upload to Flask app.")
    
    logger.info("=" * 60)
    logger.info("--- Web Database Sync Process Finished ---")
    logger.info("=" * 60)

if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("SYNC SCRIPT STARTED")
    logger.info("=" * 60)
    logger.info("Running sync_script in standalone mode for testing.")
    logger.info(f"Log file created: {log_filename}")
    logger.info(f"Python version: {os.sys.version}")
    
    # Try to check if SQL Server is accessible
    logger.info("Checking SQL Server availability...")
    try:
        import pyodbc
        drivers = pyodbc.drivers()
        logger.info(f"Available ODBC drivers: {drivers}")
    except Exception as driver_error:
        logger.warning(f"Could not enumerate ODBC drivers: {driver_error}")
    
    # List of servers to try (in order of preference)
    servers_to_try = [
        socket.gethostname(),  # Local machine first
        'sr-bft-w10-lp07',     # Old print server
        'sr-ltp-tmcgrath'      # New server
    ]
    
    # Instance names to try
    instance_names = ['SQLEXPRESS1', 'SQLEXPRESS']
    
    successful_engine = None
    successful_server = None
    successful_instance = None
    
    logger.info("=" * 60)
    logger.info("ATTEMPTING TO FIND SQL SERVER")
    logger.info("=" * 60)
    
    found = False
    for server in servers_to_try:
        if found:
            break
        for instance in instance_names:
            try:
                server_instance = f"{server}\\{instance}"
                MSSQL_CONN_STR = f"Driver={{SQL Server}};SERVER={server_instance};Database=ECADMASTER;Trusted_Connection=yes;"
                logger.info(f"Trying server: {server_instance}")
                logger.info(f"Connection string: {MSSQL_CONN_STR}")
                
                mssql_quoted_conn_str = urllib.parse.quote_plus(MSSQL_CONN_STR)
                logger.info("Creating MSSQL engine...")
                logger.info(f"SQLAlchemy connection URL: mssql+pyodbc:///?odbc_connect={mssql_quoted_conn_str}")
                test_engine = sqlalchemy.create_engine(f'mssql+pyodbc:///?odbc_connect={mssql_quoted_conn_str}')
                logger.info("[OK] MSSQL engine created successfully")
                
                # Try a test connection
                logger.info("Testing MSSQL connection...")
                with test_engine.connect() as test_conn:
                    result = test_conn.execute(sqlalchemy.text("SELECT @@VERSION"))
                    version = result.fetchone()[0]
                    logger.info(f"[SUCCESS] Connected to {server_instance}")
                    logger.info(f"[SUCCESS] SQL Server Version: {version[:100]}...")
                    successful_engine = test_engine
                    successful_server = server
                    successful_instance = instance
                    found = True
                    break  # Connection successful, exit inner loop
                    
            except Exception as conn_error:
                logger.warning(f"[FAILED] Could not connect to {server_instance}")
                logger.warning(f"[FAILED] Error: {str(conn_error)[:200]}")
                logger.info("-" * 60)
                continue  # Try next instance
    
    logger.info("=" * 60)
    
    try:
        if successful_engine:
            logger.info(f"[FINAL] Using SQL Server: {successful_server}\\{successful_instance}")
            run_sync_and_upload(successful_engine)
        else:
            logger.error("[FATAL] Could not connect to any SQL Server instance!")
            logger.error(f"[FATAL] Tried servers: {', '.join([f'{s}\\{i}' for s in servers_to_try for i in instance_names])}")
    except Exception as e:
        logger.error(f"Fatal error in main: {e}", exc_info=True)
    finally:
        logger.info(f"\nLog file saved to: {os.path.abspath(log_filename)}")
        print("\n" + "=" * 60)
        print("Press Enter to close this window...")
        print("=" * 60)
        input()  # Keep terminal open
