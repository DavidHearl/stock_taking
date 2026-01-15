import sqlite3
import csv
import io
import math

def generate_workguru_csv(system_number, cad_db_path, products_db_path):
    """
    Generates the CSV content for a single system number for WorkGuru import.
    This now includes Accessories, Glass, and Raumplus components.

    Args:
        system_number (int): The job number to process.
        cad_db_path (str): The file path to the main CAD data SQLite database.
        products_db_path (str): The file path to the products SQLite database.

    Returns:
        str: The content of the generated CSV file as a string.
    """
    output_in_memory = io.StringIO()
    csvwriter = csv.writer(output_in_memory, delimiter=',')
    csvwriter.writerow(["Sku", "Name", "Description", "CostPrice", "SellPrice", "Quantity", "Billable"])

    try:
        with sqlite3.connect(cad_db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute(f"ATTACH DATABASE '{products_db_path}' AS workguru_db")

            # --- Check for Vinyl Doors / Cornice ---
            vinyl_query = """
                SELECT *
                FROM DISTINTAT
                WHERE NUMERO = ? AND (CODCOMP like 'BM%' or CODCOMP like 'BC%')
            """
            vinyl_rows = cursor.execute(vinyl_query, (system_number,)).fetchall()
            if len(vinyl_rows) > 0:
                csvwriter.writerow(['DOR_VNL_OSD_MTM', 'OS Doors - Vinyl Doors - Made to Measure', 'OS Doors - Vinyl Doors - Made to Measure', 0, 0, 1, "FALSE"])
            
            # --- Glass Query ---
            glass_query = """
                SELECT p.wg_sku, p.name, p.description, p.cost_price, p.sell_price, SUM(d.QTACOMP) as QTY
                FROM DISTINTAT d
                JOIN articoli a ON d.CODCOMP = a.cod
                JOIN workguru_db.products p ON d.CODCOMP = p.cad_sku
                WHERE a.REPARTO = '005' AND d.NUMERO = ?
                GROUP BY p.wg_sku, p.name, p.description, p.cost_price, p.sell_price;
            """
            for row in cursor.execute(glass_query, (system_number,)):
                csvwriter.writerow([row['wg_sku'], row['name'], row['description'], row['cost_price'], row['sell_price'], row['QTY'], "FALSE"])

            # --- NEW: Raumplus Logic ---
            rp_query = """
                SELECT
                    d.CODCOMP, a.DES as Description, d.DIML, SUM(d.QTACOMP) as QTY,
                    p.wg_sku, p.name, p.cost_price, p.sell_price
                FROM DISTINTAT d
                JOIN articoli a ON d.CODCOMP = a.cod
                LEFT JOIN workguru_db.products p ON d.CODCOMP = p.cad_sku
                WHERE a.REPARTO = '004' AND d.NUMERO = ? AND d.LIVELLO > 1
                AND d.CODCOMP NOT LIKE '10.01.237'
                GROUP BY d.CODCOMP, d.DIML
                ORDER BY d.CODCOMP;
            """
            rows = cursor.execute(rp_query, (system_number,)).fetchall()

            if rows:
                # Process items that are not measured by length (e.g., wheels, screws)
                non_length_items = [r for r in rows if not r['DIML'] or r['DIML'] == 0]
                for row in non_length_items:
                    if row['wg_sku']:
                        csvwriter.writerow([row['wg_sku'], row['name'], row['Description'], row['cost_price'], row['sell_price'], row['QTY'], "FALSE"])
                    else:
                        print(f"Warning: No WorkGuru product found for Raumplus component {row['CODCOMP']}")

                # Process items that are measured by length (e.g., profiles, tracks)
                length_items = [r for r in rows if r['DIML'] and r['DIML'] > 0]
                if length_items:
                    current_code = ""
                    total_qty_needed = 0
                    total_length = 0
                    
                    # Buffer for the current product's WG details
                    wg_details = {}

                    for row in length_items:
                        # If this is a new component, write the previous one to the CSV
                        if current_code and current_code != row['CODCOMP']:
                            final_qty = total_qty_needed + (1 if total_length > 0 else 0)
                            if final_qty > 0 and wg_details:
                                csvwriter.writerow([wg_details['sku'], wg_details['name'], wg_details['desc'], wg_details['cost'], wg_details['sell'], final_qty, "FALSE"])
                            
                            # Reset for the new component
                            total_qty_needed = 0
                            total_length = 0
                        
                        # Update tracking for the current item
                        current_code = row['CODCOMP']
                        wg_details = {'sku': row['wg_sku'], 'name': row['name'], 'desc': row['Description'], 'cost': row['cost_price'], 'sell': row['sell_price']}
                        # Determine supply length based on component code
                        parts = row['CODCOMP'].split('.')
                        part1, part2, part3 = int(parts[0]), int(parts[1]), int(parts[2])
                        if 'brush' in row['name'].lower() or 'gasket' in row['name'].lower():
                            total_qty_needed = math.ceil(row['QTY'] * row['DIML'] / 1000)

                        else: 
                            maxLength = 5000
                            # Accumulate lengths to calculate how many stock lengths are needed
                            for _ in range(int(row['QTY'])):
                                total_length += row['DIML']
                                if total_length > maxLength:
                                    total_qty_needed += 1
                                    total_length = row['DIML']
                    
                    # Write the very last processed length item to the CSV
                    if current_code:
                        final_qty = total_qty_needed + (1 if total_length > 0 else 0)
                        if final_qty > 0 and wg_details and wg_details['sku']:
                             csvwriter.writerow([wg_details['sku'], wg_details['name'], wg_details['desc'], wg_details['cost'], wg_details['sell'], final_qty, "FALSE"])
                        elif not wg_details['sku']:
                            print(f"Warning: No WorkGuru product found for Raumplus component {current_code}")

            # --- Hettich Query ---
            acc_query = """
                SELECT p.wg_sku, d.CODCOMP, p.name, p.description, a.DES as CAD_des, p.cost_price, p.sell_price, SUM(d.QTACOMP) as QTY
                FROM DISTINTAT d
                JOIN articoli a ON d.CODCOMP = a.cod
                LEFT JOIN workguru_db.products p ON d.CODCOMP = p.cad_sku
                WHERE a.REPARTO IN ('006') AND d.NUMERO = ?
                GROUP BY p.wg_sku, d.CODCOMP, p.name, p.description, p.cost_price, p.sell_price
            """
            for row in cursor.execute(acc_query, (system_number,)):
                if row['wg_sku']:
                    csvwriter.writerow([row['wg_sku'], row['name'], row['description'], row['cost_price'], row['sell_price'], row['QTY'], "FALSE"])
                else:
                    if row['CODCOMP'] == '9121847':
                        csvwriter.writerow(['DRW_SET_HAF_516.24.304', 
                                            'Matrix Drawer - 450mmD x 167mmH Black', 
                                            'Matrix Box S Slim Drawer Set 35 kg 167 mm High Soft and Smooth Closing', 
                                            13.5, 
                                            0, 
                                            row['QTY'], 
                                            "FALSE"])
                    elif row['CODCOMP'] == '9150505':
                        csvwriter.writerow(['DRW_SET_HAF_516.25.304', 
                                            'Matrix Drawer - 450mmD x 199mmH Black', 
                                            'Matrix Box S Slim Drawer Set 35 kg 199 mm High Soft and Smooth Closing', 
                                            14.75, 
                                            0, 
                                            row['QTY'], 
                                            "FALSE"])
                    elif row['CODCOMP'] == '9150501':
                        csvwriter.writerow(['DRW_SET_HAF_516.20.304', 
                                            'Matrix Drawer - 450mmD x 89mmH Black', 
                                            'Matrix Box S Slim Drawer Set 35 kg 89 mm High Soft and Smooth Closing', 
                                            11, 
                                            0, 
                                            row['QTY'], 
                                            "FALSE"])
                    elif 'Arcitech' in row['CAD_des'] or 'Actro' in row['CAD_des']:
                        continue
                    else:
                        csvwriter.writerow([row['CODCOMP'], row['CAD_des'], row['QTY'], 'MISSING'])

            # --- Accessories Query ---
            acc_query = """
                SELECT p.wg_sku, d.CODCOMP, p.name, p.description, a.DES as CAD_des, p.cost_price, p.sell_price, SUM(d.QTACOMP) as QTY
                FROM DISTINTAT d
                JOIN articoli a ON d.CODCOMP = a.cod
                LEFT JOIN workguru_db.products p ON d.CODCOMP = p.cad_sku
                WHERE a.REPARTO IN ('002', '003') AND d.NUMERO = ?
                GROUP BY p.wg_sku, d.CODCOMP, p.name, p.description, p.cost_price, p.sell_price
            """
            for row in cursor.execute(acc_query, (system_number,)):
                if row['wg_sku']:
                    csvwriter.writerow([row['wg_sku'], row['name'], row['description'], row['cost_price'], row['sell_price'], row['QTY'], "FALSE"])
                else:
                    if 'SC147' in row['CODCOMP']:
                        csvwriter.writerow(['DRW_SET_HAF_516.24.304', 
                                            'Matrix Drawer - 450mmD x 167mmH Black', 
                                            'Matrix Box S Slim Drawer Set 35 kg 167 mm High Soft and Smooth Closing', 
                                            13.5, 
                                            0, 
                                            row['QTY'], 
                                            "FALSE"])
                    elif 'SC327' in row['CODCOMP']:
                        csvwriter.writerow(['DRW_SET_HAF_516.25.304', 
                                            'Matrix Drawer - 450mmD x 199mmH Black', 
                                            'Matrix Box S Slim Drawer Set 35 kg 199 mm High Soft and Smooth Closing', 
                                            14.75, 
                                            0, 
                                            row['QTY'], 
                                            "FALSE"])
                    elif 'SC73' in row['CODCOMP']:
                        csvwriter.writerow(['DRW_SET_HAF_516.20.304', 
                                            'Matrix Drawer - 450mmD x 89mmH Black', 
                                            'Matrix Box S Slim Drawer Set 35 kg 89 mm High Soft and Smooth Closing', 
                                            11, 
                                            0, 
                                            row['QTY'], 
                                            "FALSE"])
                    else:
                        csvwriter.writerow([row['CODCOMP'], row['CAD_des'], row['QTY'], 'MISSING'])

            cursor.execute("DETACH DATABASE workguru_db")

    except sqlite3.Error as e:
        print(f"Database error for system number {system_number}: {e}")
        csvwriter.writerow([f"ERROR: {e}", "", "", "", "", "", ""])

    output_in_memory.seek(0)
    return output_in_memory.read()