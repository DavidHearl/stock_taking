import psutil, subprocess, time, datetime, pyodbc, socket
import sqlalchemy, urllib
from sync_script import run_sync_and_upload

# Loop until interrupted
while True:
    ecadpro_flag = 0
    now = datetime.datetime.now()
    print(now) # Log time to console
    if now.hour < 21:
        # print("Start")
        # Check active processes for ecadpro
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                if proc.name() == 'ecadpro.exe':
                    # If it's running
                    ecadpro_flag = 1 
            except:
                continue

        if ecadpro_flag == 0:
            # If not currently running - start it up
            print("3cad is not running")
            subprocess.Popen("C:\\evolution\\eCadPro\\eCadPro.exe /C SLIDEROBES /START PrintSrvPro.crea,auto /D /L")
            # subprocess.Popen("C:\evolution\eCadPro\ecadpro.exe")
        elif ecadpro_flag == 1:
            # If it is running, do nothing
            print("3cad is running")
        # print("End")
        time.sleep(300)

    else:
        try:
            # --- CREATE ONE ENGINE FOR ALL TASKS ---
            print("Connecting to MS SQL with SQLAlchemy...")
            conn_string = f"Driver={{SQL Server}};SERVER={socket.gethostname()}\\SQLEXPRESS1;Database=ECADMASTER;Trusted_Connection=yes;"
            quoted_conn_str = urllib.parse.quote_plus(conn_string)
            engine = sqlalchemy.create_engine(f'mssql+pyodbc:///?odbc_connect={quoted_conn_str}')

            # Use a single 'with' block for the connection
            with engine.connect() as connection:
                # --- TASK 1: Perform the existing MS SQL Backup ---
                print("Starting MS SQL .bak file backup...")
                # SQLAlchemy needs to run backups in a raw connection with autocommit
                connection.execution_options(autocommit=True).execute(
                    sqlalchemy.text("BACKUP DATABASE ECADMASTER TO DISK = 'C:\\evolution\\eCadPro\\_bak\\ECADMASTER.bak' WITH INIT;")
                )
                print("MS SQL .bak file backup created successfully.")

                # --- TASK 2: Perform the new SQLite export and upload ---
                # Pass the already-open engine to your function
                run_sync_and_upload(engine)

        except Exception as error:
            print("An error occurred during the nightly backup/sync process:")
            print(error)

        print("Going to sleep for 3 hours") # Until after midnight when Print Server will start up again.
        time.sleep(10800)
        # time.sleep(30)