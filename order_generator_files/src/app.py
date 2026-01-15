from flask import Flask, render_template, request, send_file, jsonify, redirect, url_for
import io
import os
from dotenv import load_dotenv
import zipfile
import sqlite3
from datetime import datetime
import requests
from src.board_logic import generate_board_order_file
from src.workguru_logic import generate_workguru_csv

# Get the absolute path to the directory where this script (app.py) is located (e.g., /.../src)
APP_DIR = os.path.dirname(os.path.abspath(__file__))
# Get the path to the project's root directory (one level up from 'src')
PROJECT_ROOT = os.path.dirname(APP_DIR)
# Construct the full, absolute path to the .env file in the project root
DOTENV_PATH = os.path.join(PROJECT_ROOT, '.env')
# Explicitly load the .env file from that specific path
load_dotenv(dotenv_path=DOTENV_PATH)


# Now, build absolute paths to your database files.
# This ensures the app always looks for them in the correct 'src' folder.
DATABASE_PATH = os.path.join(PROJECT_ROOT, 'cad_data.db')
PRODUCTS_DB_PATH = os.path.join(APP_DIR, 'products.db')

app = Flask(__name__)

# Secret Key must be the same as in sync_script.py on the print server.
APP_SECRET_KEY = 'a-very-strong-and-secret-password' # IMPORTANT: Change this!

# --- Define the routes (URLs) for the application ---

# This route shows the main page with the form
@app.route('/')
def index():
    # Define default values in case the database doesn't exist yet
    db_stats = {
        'min_date': 'N/A',
        'max_date': 'N/A',
        'min_num': 'N/A',
        'max_num': 'N/A',
        'last_update': 'Never'
    }

    try:
        # 1. Get the last update time of the database file
        timestamp = os.path.getmtime(DATABASE_PATH)
        db_stats['last_update'] = datetime.fromtimestamp(timestamp).strftime('%d %B %Y at %H:%M:%S')

        # 2. Connect to the database and get the min/max values
        with sqlite3.connect(DATABASE_PATH) as conn:
            cursor = conn.cursor()
            query = "SELECT MIN(DATA), MAX(DATA), MIN(NUMERO), MAX(NUMERO) FROM TORDINE"
            min_date, max_date, min_num, max_num = cursor.execute(query).fetchone()
            
            # Format the dates nicely (optional)
            if min_date:
                db_stats['min_date'] = datetime.strptime(min_date.split(' ')[0], '%Y-%m-%d').strftime('%d %b %Y')
            if max_date:
                db_stats['max_date'] = datetime.strptime(max_date.split(' ')[0], '%Y-%m-%d').strftime('%d %b %Y')
            
            db_stats['min_num'] = min_num
            db_stats['max_num'] = max_num

    except (FileNotFoundError, sqlite3.Error) as e:
        # If the file doesn't exist or there's a DB error, the defaults will be used
        print(f"Could not load database stats: {e}")

    # Renders an HTML file located in a 'templates' folder
    return render_template('index.html', stats=db_stats)

# This route generates the PNX file with the Boards for all jobs.
@app.route('/generate', methods=['POST'])
def generate():
    # Get data from the form
    po_label = request.form['po_label']
    system_numbers = request.form['system_numbers']

    if not po_label or not system_numbers:
        return "Error: PO Label and System Numbers are required.", 400

    # Call your logic function
    pnx_content = generate_board_order_file(system_numbers, DATABASE_PATH)

    # Prepare the file for download
    file_for_download = io.BytesIO(pnx_content.encode('utf-8'))

    return send_file(
        file_for_download,
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'Boards Order {po_label}.pnx'
    )

# This route generates CSV files for each job in the list that can then be imported to the WorkGuru Projects
@app.route('/generate_wg_csv', methods=['POST'])
def generate_wg_csv():
    """
    Handles the form submission for generating WorkGuru CSV files.
    Creates one CSV per system number and downloads them as a single zip file.
    """
    system_numbers_str = request.form.get('system_numbers', '')
    if not system_numbers_str:
        # Handle case where textarea is empty
        return "Error: No system numbers provided.", 400

    numList = [int(n.strip()) for n in system_numbers_str.splitlines() if n.strip()]

    if len(numList) == 1:
        # --- SINGLE FILE LOGIC ---
        sysNum = numList[0]
        print(f"Generating single WorkGuru CSV for system number: {sysNum}")
        
        # Call the logic function to get the CSV content
        csv_content = generate_workguru_csv(sysNum, DATABASE_PATH, PRODUCTS_DB_PATH)
        
        # Prepare the file for download in memory
        memory_file = io.BytesIO(csv_content.encode('utf-8'))
        
        # Send the single CSV file to the user's browser
        return send_file(
            memory_file,
            mimetype='text/csv',
            as_attachment=True,
            download_name=f"WG_Products_Import_{sysNum}.csv"
        )
    
    else:
        # --- MULTIPLE FILES LOGIC (existing zip logic) ---
        print(f"Generating zip archive for {len(numList)} WorkGuru CSVs...")
        memory_file = io.BytesIO()

        with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            for sysNum in numList:
                csv_content = generate_workguru_csv(sysNum, DATABASE_PATH, PRODUCTS_DB_PATH)
                file_name = f"WG_Products_Import_{sysNum}.csv"
                zf.writestr(file_name, csv_content)

        memory_file.seek(0)

        return send_file(
            memory_file,
            mimetype='application/zip',
            as_attachment=True,
            download_name='WorkGuru_Imports.zip'
        )

@app.route('/dbupdate', methods=['POST'])
def db_update():
    # 1. Security Check: Ensure the request comes from our trusted script
    api_key = request.headers.get('X-API-Key')
    if api_key != APP_SECRET_KEY:
        return jsonify({'error': 'Unauthorized access'}), 403 # Forbidden

    # 2. File Check: Ensure a file was actually sent
    if 'database' not in request.files:
        return jsonify({'error': 'No file part in the request'}), 400 # Bad Request
    
    file = request.files['database']

    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    if file:
        # 3. Safe Replace: Save to a temporary file first, then rename.
        # This prevents the app from trying to read a half-written DB file.
        temp_path = DATABASE_PATH + '.tmp'
        file.save(temp_path)
        
        # Atomically replace the old database with the new one
        os.replace(temp_path, DATABASE_PATH)
        
        print("Database updated successfully.")
        return jsonify({'status': 'success', 'message': 'Database updated.'}), 200
        
    return jsonify({'error': 'An unknown error occurred'}), 500

@app.route('/products/import', methods=['GET', 'POST'])
def import_products_from_wg():
    # Read the keys from environment variables for security
    api_key = os.environ.get('WORKGURU_API_KEY')
    secret_key = os.environ.get('WORKGURU_SECRET_KEY')

    if request.method == 'POST':
        if not api_key or not secret_key:
            return "Error: WORKGURU_API_KEY and WORKGURU_SECRET_KEY must be configured on the server.", 500

        # --- STEP 1: Authenticate with WorkGuru to get a temporary access token ---
        access_token = None
        try:
            auth_url = "https://ukapi.workguru.io/api/ClientTokenAuth/Authenticate/api/client/v1/tokenauth"
            auth_payload = {"apiKey": api_key, "secret": secret_key}
            
            print("Authenticating with WorkGuru...")
            auth_response = requests.post(auth_url, json=auth_payload)
            auth_response.raise_for_status()  # Check for HTTP errors
            
            auth_data = auth_response.json()
            access_token = auth_data.get("accessToken")

            if not access_token:
                # Log the actual response for debugging if the token is missing
                print(f"Authentication failed. API Response: {auth_data}")
                return "Authentication failed: Could not retrieve access token from WorkGuru.", 500
            
            print("Successfully authenticated.")

        except requests.exceptions.RequestException as e:
            return f"Error authenticating with WorkGuru API: {e}", 500

        # --- STEP 2: Fetch all products using the new access token, handling pagination ---
        all_wg_products = []
        skip_count = 0
        max_results = 100
        
        print("Fetching products from WorkGuru...")
        while True:
            api_url = f"https://ukapi.workguru.io/api/services/app/Product/GetProducts?IsActive=True&MaxResultCount={max_results}&SkipCount={skip_count}"
            # Use the access token in the Authorization header
            headers = {'Authorization': f'Bearer {access_token}'}
            
            try:
                response = requests.get(api_url, headers=headers)
                response.raise_for_status()
                data = response.json()
                
                items = data.get('result', {}).get('items', [])
                if not items:
                    break
                
                all_wg_products.extend(items)
                skip_count += len(items)
                
            except requests.exceptions.RequestException as e:
                return f"Error fetching products from WorkGuru API: {e}", 500
        
        print(f"Fetched a total of {len(all_wg_products)} products.")

        # --- STEP 3: Get all existing SKUs from our local products.db ---
        with sqlite3.connect(PRODUCTS_DB_PATH) as conn:
            existing_skus = {row[0] for row in conn.execute('SELECT wg_sku FROM products')}

        # --- STEP 4: Filter the list to find only new products ---
        new_products = [
            p for p in all_wg_products if p.get('sku') and p.get('sku') not in existing_skus
        ]
        
        print(f"Found {len(new_products)} new products to import.")
        
        return render_template('import_products.html', new_products=new_products)

    # For a GET request, just show the initial page
    return render_template('import_products.html', new_products=None)

# --- READ: Show a list of all products ---
@app.route('/products')
def list_products():
    # Get the search query from the URL's 'q' parameter
    search_query = request.args.get('q', '').strip()
    
    with sqlite3.connect(PRODUCTS_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        
        if search_query:
            # If a search query exists, filter the results
            search_term = f"%{search_query}%"
            query = """
                SELECT * FROM products 
                WHERE wg_sku LIKE ? OR name LIKE ? OR description LIKE ?
                ORDER BY name
            """
            products = conn.execute(query, (search_term, search_term, search_term)).fetchall()
        else:
            # If no search query, get all products
            query = "SELECT * FROM products ORDER BY name"
            products = conn.execute(query).fetchall()
            
    # Pass the search query back to the template to display it in the search bar
    return render_template('products.html', products=products, search_query=search_query)

# --- UPDATE: Edit an existing product ---
@app.route('/products/edit/<sku>', methods=['GET', 'POST'])
def edit_product(sku):
    with sqlite3.connect(PRODUCTS_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if request.method == 'POST':
            # Get all data from the form
            updated_product = {
                'name': request.form['name'],
                'cad_sku': request.form['cad_sku'],
                'description': request.form['description'],
                'cost_price': request.form['cost_price'],
                'sell_price': request.form['sell_price'],
                'wg_sku': sku  # The SKU to update
            }
            
            # Prepare and execute the full UPDATE query
            update_query = """
                UPDATE products SET
                    name = :name,
                    cad_sku = :cad_sku,
                    description = :description,
                    cost_price = :cost_price,
                    sell_price = :sell_price
                WHERE wg_sku = :wg_sku
            """
            conn.execute(update_query, updated_product)
            conn.commit()
            return redirect(url_for('list_products'))
        
        # For a GET request, fetch the product and show the form
        product = conn.execute('SELECT * FROM products WHERE wg_sku = ?', (sku,)).fetchone()
        return render_template('product_form.html', product=product, title="Edit Product")

# --- CREATE: Add a new product ---
@app.route('/products/add', methods=['GET', 'POST'])
def add_product():
    if request.method == 'POST':
        with sqlite3.connect(PRODUCTS_DB_PATH) as conn:
            # Get all data from the form
            new_product = {
                'wg_sku': request.form['wg_sku'],
                'cad_sku': request.form['cad_sku'],
                'name': request.form['name'],
                'description': request.form['description'],
                'cost_price': request.form.get('cost_price', 0.0),
                'sell_price': request.form.get('sell_price', 0.0)
            }

            # Prepare and execute the full INSERT statement
            # Note: Add other column names here if you add them to the dictionary above
            insert_query = """
                INSERT INTO products (wg_sku, cad_sku, name, description, cost_price, sell_price)
                VALUES (:wg_sku, :cad_sku, :name, :description, :cost_price, :sell_price)
            """
            conn.execute(insert_query, new_product)
            conn.commit()
        return redirect(url_for('list_products'))
    
    # For a GET request, show a blank form
    return render_template('product_form.html', product=None, title="Add New Product")

@app.route('/products/batch_add', methods=['POST'])
def batch_add_products():
    # --- This route handles the submission of the new products form ---
    
    # We get all the form data as a dictionary
    form_data = request.form.to_dict()
    
    # We need to restructure the flat form data back into a list of product dictionaries
    products_to_add = []
    # Find the highest index from the form keys (e.g., 'sku_0', 'sku_1' -> 1)
    max_index = max([int(k.split('_')[-1]) for k in form_data.keys()])

    for i in range(max_index + 1):
        # Check if the 'include' checkbox for this product was checked
        if f'include_{i}' in form_data:
            product = {
                'wg_sku': form_data.get(f'sku_{i}'),
                'cad_sku': form_data.get(f'cad_sku_{i}'),
                'name': form_data.get(f'name_{i}'),
                'description': form_data.get(f'description_{i}'),
                'cost_price': form_data.get(f'cost_price_{i}', 0.0),
                'sell_price': form_data.get(f'sell_price_{i}', 0.0),
                'supplier_name': form_data.get(f'supplier_{i}'),
                'brand': form_data.get(f'brand_{i}'),
                'category': form_data.get(f'category_{i}'),
            }
            products_to_add.append(product)

    if products_to_add:
        with sqlite3.connect(PRODUCTS_DB_PATH) as conn:
            cursor = conn.cursor()
            # Prepare the INSERT statement
            insert_query = """
                INSERT INTO products (wg_sku, cad_sku, name, description, cost_price, sell_price, supplier_name, brand, category)
                VALUES (:wg_sku, :cad_sku, :name, :description, :cost_price, :sell_price, :supplier_name, :brand, :category)
            """
            # Use executemany for an efficient bulk insert
            cursor.executemany(insert_query, products_to_add)
            conn.commit()

    return redirect(url_for('list_products'))

# --- DELETE: Remove a product ---
@app.route('/products/delete/<sku>', methods=['POST'])
def delete_product(sku):
    with sqlite3.connect(PRODUCTS_DB_PATH) as conn:
        conn.execute('DELETE FROM products WHERE wg_sku = ?', (sku,))
        conn.commit()
    return redirect(url_for('list_products'))

if __name__ == '__main__':
    app.run(debug=True)