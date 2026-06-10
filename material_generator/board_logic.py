import sqlite3
import csv
import io
import logging
import contextlib

logger = logging.getLogger(__name__)


def _board_system_diagnostics(conn, sysNum):
    """Collect counts that explain why a system may produce no board rows.

    Returns a dict with component counts and any human-readable warnings.
    Never raises - diagnostics must not break generation.
    """
    info = {
        'system': sysNum,
        'total_components': 0,
        'board_components': 0,
        'missing_codes': 0,
        'board_rows': 0,
        'messages': [],
    }
    try:
        cur = conn.cursor()
        info['total_components'] = cur.execute(
            "SELECT COUNT(*) FROM DISTINTAT WHERE numero = ?", (sysNum,)
        ).fetchone()[0]
        info['board_components'] = cur.execute(
            "SELECT COUNT(*) FROM DISTINTAT d WHERE numero = ? AND "
            "(SELECT reparto FROM articoli WHERE cod LIKE d.CODCOMP) = '001'",
            (sysNum,)
        ).fetchone()[0]
        info['missing_codes'] = cur.execute(
            "SELECT COUNT(DISTINCT CODCOMP) FROM DISTINTAT WHERE numero = ? AND "
            "CODCOMP NOT IN (SELECT cod FROM articoli WHERE cod IS NOT NULL)", (sysNum,)
        ).fetchone()[0]
    except Exception as e:
        logger.error(f"System {sysNum}: board diagnostics failed: {e}")
        info['messages'].append(f"System {sysNum}: diagnostic check failed ({e}).")
    return info


def _count_rows_per_system(content, order_col=11, delimiter=';'):
    """Count produced data rows grouped by the ORDERNAME column (system number)."""
    counts = {}
    reader = csv.reader(io.StringIO(content), delimiter=delimiter)
    next(reader, None)  # skip header
    for row in reader:
        if not row or len(row) <= order_col:
            continue
        key = str(row[order_col]).strip()
        if key:
            counts[key] = counts.get(key, 0) + 1
    return counts


def generate_board_order_file(system_numbers_str, conn, summary=None):
    """Generate PNX board order file from system numbers.

    If a mutable ``summary`` dict is supplied, it is populated with a per-system
    breakdown (component counts, missing reference codes, rows generated and any
    warnings) so callers can show the user exactly what happened.
    """
    logger.info(f"Starting board order file generation")
    logger.info(f"Raw system numbers input: {system_numbers_str}")
    
    # Use io.StringIO to build the CSV in memory instead of writing to a file
    output_in_memory = io.StringIO()
    csvwriter = csv.writer(output_in_memory, delimiter=';')

    # 1. Parse the system_numbers_str into a list of numbers
    numList = [int(n.strip()) for n in system_numbers_str.splitlines() if n.strip()]
    logger.info(f"Parsed system numbers: {numList}")
    logger.info(f"Total system numbers to process: {len(numList)}")

    # 2. Use the provided in-memory connection
    try:
        with contextlib.nullcontext(conn):
            logger.info("Database connection established")
            conn.row_factory = sqlite3.Row 

            # 3. Write the header
            new_header = [
                'SPARE', 'BARCODE', 'MATNAME', 'CLENG', 'CWIDTH', 'CNT', 'OVERS', 'UNDERS', 
                'GRAIN', 'QUICKEDGE0', 'CUSTOMER', 'ORDERNAME', 'ARTICLENAME', 'PARTDESC', 
                'PRFID1', 'PRFID3', 'PRFID4', 'PRFID2', 'EDGINGCORNERSPEC', 'TOPSURFACE', 
                'BOTSURFACE', 'BARCODE_1', 'PROCESSINGNOTE', 'DESTACKING', 'UNUSED', 
                'FIN LxW', '2NDCUT', 'GRAINMATCH', 'spare.1', 'spare_2', 'LabelPrintingMode', 
                'LabelTemplateName', 'PictureFileName', 'spare_3', 'spare_4', 'OPTIMISINGPARAM', 
                'SAWPARAM', 'WORKPIECETYPE', 'ID', 'MAGICUTID', 'DRAWINGPATH', 'POSNUMBER', 
                '2ndCNC', 'spare_5', 'MPRNAME', 'ROUTING', 'EDGING_FOR_ROUTING', 'PLAN_POS', 'Column1'
            ]
            csvwriter.writerow(new_header)
            logger.info("Header row written to PNX file")

            # Pre-pass diagnostics so we can tell the user why a job may be empty
            diagnostics = {}
            if summary is not None:
                for sysNum in numList:
                    diagnostics[sysNum] = _board_system_diagnostics(conn, sysNum)
                    d = diagnostics[sysNum]
                    logger.info(
                        f"System {sysNum} diagnostics - components: {d['total_components']}, "
                        f"board components: {d['board_components']}, missing codes: {d['missing_codes']}"
                    )

            N3_excl = "'','U961 ST2 Graphite Grey','X - Scrap','Richmond','Richmond Open','Tullymore','Tullymore Open','Venice','Aldridge'"
            max_rip_length = 2750

            # 4. Loop through numList and run all your queries
            for sysNum in numList:
                logger.info(f"Processing system number: {sysNum}")
                customer_query = "select CONTRACT, CUSTOMER from TORDINE where numero = " + str(sysNum)
                
                try:
                    cust_cursor = conn.cursor()
                    cust_cursor.execute(customer_query)
                    cust_details = cust_cursor.fetchone()
                    
                    if cust_details is None:
                        logger.warning(f"No customer details found in TORDINE for system {sysNum}, trying CUSTOMERID")
                        customer_query = "select CONTRACT, CUSTNAME as [CUSTOMER] from CUSTOMERID where numero = " + str(sysNum)
                        cust_cursor = conn.cursor()
                        cust_cursor.execute(customer_query)
                        cust_details = cust_cursor.fetchone()
                    
                    if cust_details is None:
                        logger.error(f"No customer details found for system {sysNum} in either TORDINE or CUSTOMERID")
                        row_ref = f"Unknown_{sysNum}"
                    else:
                        contract = cust_details['contract'] if cust_details['contract'] else ""
                        customer = cust_details['customer'] if cust_details['customer'] else ""
                        row_ref = f"{contract} {customer}".strip()
                        logger.info(f"Customer reference for {sysNum}: {row_ref}")
                        
                except Exception as e:
                    logger.error(f"Error getting customer details for {sysNum}: {e}")
                    row_ref = f"Error_{sysNum}"
                
                # Count Number of distinct colours included in Jobs list
                ColCountQuery = "SELECT COUNT(DISTINCT [N3]) FROM DISTINTAT where numero = " + str(sysNum) + " AND N3 NOT IN (" + N3_excl + ") and N3 not like 'BM%' and N3 not like 'Sibu%' and CODCOMP not in ('SCOOPFRONT','ASCARIFRONT','PTHRTN') and DIMP<=36 and (select reparto from articoli where cod like codcomp ) ='001'"

                BSumCursor = conn.cursor()
                BSumCursor.execute(ColCountQuery)
                ColCount = BSumCursor.fetchone()[0]
                logger.info(f"System {sysNum}: Found {ColCount} distinct colours")
                
                Colour_List = []
                if ColCount > 0:
                    
                    tempQuery = "SELECT DISTINCT [N3] FROM DISTINTAT where numero = " + str(sysNum) + " AND N3 NOT in (" + N3_excl + ") and N3 not like 'BM%' and N3 not like 'Sibu%' and CODCOMP not in ('SCOOPFRONT','ASCARIFRONT','PTHRTN') and DIMP<=36 and (select reparto from articoli where cod like codcomp ) ='001'"
                    BSumCursor.execute(tempQuery)
                    rows = BSumCursor.fetchall()
                    for row in rows:
                        Colour_List.append(row[0])
                    logger.info(f"System {sysNum}: Colour list: {Colour_List}")
                else:
                    logger.warning(f"System {sysNum}: No colours found - this system may have no board data or doesn't match filter criteria")

                for Colour in Colour_List:
                    egger_code = Colour.split(' ')[0]
                    st_number = ''
                    try:
                        st_number = Colour.split(' ')[1]
                    except:
                        print(Colour)
                    if st_number == 'ST9':
                        grain='N'
                    else:
                        grain='Y'
                    
                    MFC_code = "SHT_MFC_EGG_" + egger_code + st_number + "_18_"
                    
                    tempQuery = "SELECT COUNT(DISTINCT [DIMA]) FROM DISTINTAT where numero = " + str(sysNum) + " and N3 = '" + Colour + "' AND N6 LIKE 'E1%' and N6 NOT LIKE '%E%E%' and DIMP<=18 and (select reparto from articoli where cod like codcomp ) ='001'"
                    BSumCursor.execute(tempQuery)
                    E1_Count = int(BSumCursor.fetchone()[0])

                    if E1_Count>0:
                        tempQuery = "SELECT MAX(DIMA) FROM DISTINTAT where numero = " + str(sysNum) + " AND N3 NOT IN (" + N3_excl + ") and N3 not like 'BM%' and N3 not like 'Sibu%' and CODCOMP not in ('SCOOPFRONT','ASCARIFRONT','DRWFRONT','PTHRTN') and DIMP<=18 and (select reparto from articoli where cod like codcomp ) ='001' AND N6 LIKE '%E1%' and N6 NOT LIKE '%E%E%'"
                        BSumCursor.execute(tempQuery)
                        MaxE1_Size = int(BSumCursor.fetchone()[0])

                        if MaxE1_Size <= 1000:
                            E1_SizeList = [0, 250, 500, 680, 750, 1000]
                        else:
                            E1_SizeList = [0, 250, 500, 680, 750, MaxE1_Size]
                        E1_board_totals = [0, 0, 0, 0, 0]
                        E1_board_running_length = [0, 0, 0, 0, 0]

                        tempQuery = "SELECT * FROM DISTINTAT where numero = " + str(sysNum) + " and N3 = '" + Colour + "' and CODCOMP not in ('SCOOPFRONT','ASCARIFRONT','DRWFRONT','PTHRTN') and N6 LIKE '%E1%' and N6 NOT LIKE '%E%E%' and CODCOMP NOT LIKE 'BACK%' and DIMP<=18 and (select reparto from articoli where cod like codcomp ) ='001'"
                        BSumCursor.execute(tempQuery)
                        rows = BSumCursor.fetchall()
                        for row in rows:
                            for i in range(len(E1_board_totals)):
                                if row['dima'] <= E1_SizeList[i+1]:
                                    for x in range(int(row['qtacomp'])):
                                        E1_board_running_length[i]+= row['diml']
                                        if E1_board_running_length[i] > max_rip_length:
                                            E1_board_totals[i]+=1
                                            E1_board_running_length[i] = row['diml']
                                    break
                    
                        for i in range(len(E1_board_totals)):
                            if E1_board_running_length[i] > 0:
                                E1_board_totals[i]+=1
                        
                            if E1_board_totals[i]>0:
                                try:
                                    # Create a list with the correct number of columns
                                    row_data = [""] * len(new_header)
                                
                                    # Populate data into the correct columns for the new format
                                    row_data[1] = "Board" # BARCODE
                                    row_data[2] = MFC_code # MATNAME
                                    row_data[3] = 2800 # CLENG
                                    row_data[4] = E1_SizeList[i+1] # CWIDTH
                                    row_data[5] = E1_board_totals[i] # CNT
                                    row_data[8] = grain # GRAIN
                                    row_data[10] = row_ref # CUSTOMER
                                    row_data[11] = sysNum # ORDERNAME
                                    row_data[13] = "Board" # PARTDESC
                                    row_data[18] = ":::" # EDGINCORNERSPEC
                                
                                    if E1_SizeList[i+1] == 250: # E2L
                                        row_data[14] = "Sliderobe_Edge_08" # PRFID1 (L1)
                                        row_data[15] = "Sliderobe_Edge_08" # PRFID3 (L2)
                                    else: # E1L
                                        row_data[14] = "Sliderobe_Edge_08" # PRFID1 (L1)

                                    row_data[35] = "S" # OPTIMISINGPARAM
                                    row_data[36] = "S" # SAWPARAM
                                    row_data[45] = "Saw,Edging,Dispatch" # ROUTING
                                
                                    csvwriter.writerow(row_data)

                                except:
                                    print("Failed to write row (E1):")
                                    # print the new row_data format for debugging
                                    print(row_data)
                
                    tempQuery = "SELECT COUNT(DISTINCT [DIMA]) FROM DISTINTAT where numero = " + str(sysNum) + " and N3 = '" + Colour + "' AND N6 LIKE 'E2%' and N6 NOT LIKE '%E%E%' and DIMP<=18 and (select reparto from articoli where cod like codcomp ) ='001'"
                    BSumCursor.execute(tempQuery)
                    E2_Count = int(BSumCursor.fetchone()[0])

                    if E2_Count > 0:
                        E2_SizeList = []
                        E2_board_totals = []
                        E2_board_running_length = []
                        tempQuery = "SELECT DISTINCT [DIMA] FROM DISTINTAT where numero = " + str(sysNum) + " and N3 = '" + Colour + "' AND N6 LIKE 'E2%' and N6 NOT LIKE '%E%E%' and DIMP<=18 and (select reparto from articoli where cod like codcomp ) ='001'"
                        BSumCursor.execute(tempQuery)
                        E2_Sizes = BSumCursor.fetchall()
                        for row in E2_Sizes:
                            E2_SizeList.append(int(row[0]))
                            E2_board_totals.append(0)
                            E2_board_running_length.append(0)
                    
                        tempQuery = "SELECT * FROM DISTINTAT where numero = " + str(sysNum) + " and N3 = '" + Colour + "' and CODCOMP not in ('SCOOPFRONT','ASCARIFRONT','DRWFRONT','PTHRTN') and N6 LIKE '%E2%' and N6 NOT LIKE '%E%E%' and CODCOMP NOT LIKE 'BACK%' and DIMP<=18 and (select reparto from articoli where cod like codcomp ) ='001'"
                        BSumCursor.execute(tempQuery)
                        rows = BSumCursor.fetchall()

                        for row in rows:
                            for i in range(len(E2_board_totals)):
                                if row['dima'] == E2_SizeList[i]:
                                    for x in range(int(row['qtacomp'])):
                                        E2_board_running_length[i]+= row['diml']
                                        if E2_board_running_length[i] > max_rip_length:
                                            E2_board_totals[i]+=1
                                            E2_board_running_length[i] = row['diml']
                                    break
                    
                        for i in range(len(E2_board_totals)):
                            if E2_board_running_length[i] > 0:
                                E2_board_totals[i]+=1
                        
                            if E2_board_totals[i]>0:
                                try:
                                    row_data = [""] * len(new_header)
                                
                                    row_data[1] = "Board" # BARCODE
                                    row_data[2] = MFC_code # MATNAME
                                    row_data[3] = 2800 # CLENG
                                    row_data[4] = E2_SizeList[i] # CWIDTH
                                    row_data[5] = E2_board_totals[i] # CNT
                                    row_data[8] = grain # GRAIN
                                    row_data[10] = row_ref # CUSTOMER
                                    row_data[11] = sysNum # ORDERNAME
                                    row_data[13] = "Board" # PARTDESC
                                    row_data[14] = "Sliderobe_Edge_08" # PRFID1 (L1)
                                    row_data[15] = "Sliderobe_Edge_08" # PRFID3 (L2)
                                    row_data[18] = ":::" # EDGINCORNERSPEC
                                    row_data[35] = "S" # OPTIMISINGPARAM
                                    row_data[36] = "S" # SAWPARAM
                                    row_data[45] = "Saw,Edging,Dispatch" # ROUTING
                                
                                    csvwriter.writerow(row_data)

                                except:
                                    print("Failed to write row (E2):")
                                    print(row_data)

                    tempQuery = "SELECT COUNT(DISTINCT [DIMA]) FROM DISTINTAT where numero = " + str(sysNum) + " and N3 = '" + Colour + "' AND lower(N6) IN ('', 'unedged', 'panel') AND N3 NOT in (" + N3_excl + ") and DIMP<=18 and (select reparto from articoli where cod like codcomp ) ='001'" # and (CODCOMP not in ('LINFILL','RINFILL','LBAT','RBAT','PTHRTN','FLRRTN') or DIMA>=500)"
                    BSumCursor.execute(tempQuery)
                    Unedged_Count = int(BSumCursor.fetchone()[0])

                    if Unedged_Count > 0:
                        tempQuery = "SELECT * FROM DISTINTAT where numero = " + str(sysNum) + " and N3 = '" + Colour + "' AND lower(N6) IN ('', 'unedged', 'panel') AND N3 NOT in (" + N3_excl + ") and DIMP<=18 and (select reparto from articoli where cod like codcomp ) ='001'" # and (CODCOMP not in ('LINFILL','RINFILL','LBAT','RBAT','PTHRTN','FLRRTN') or DIMA>=500)"
                        BSumCursor.execute(tempQuery)
                        rows = BSumCursor.fetchall()
                    
                        # Unedged Boards now ordered as 1000s with E2L
                        # Anything over 1000 needs to be split in half with two edges meeting in middle
                    
                        # =================================================================
                        # START: New logic for calculating unedged board requirements
                        # =================================================================
                        all_parts = []
                        max_board_width = 1000 # The width of the boards we are ordering
                    
                        # 1. Create a flat list of all individual parts required.
                        for row in rows:
                            num_pieces = int(row['qtacomp'])
                            part_length = row['diml']
                            part_width = row['dima']

                            if part_width > max_board_width:
                                # As per the rule, split wide boards into two.
                                new_width = part_width / 2
                                # We now need twice as many pieces, each at the new width.
                                for _ in range(num_pieces * 2):
                                    all_parts.append({'length': part_length, 'width': new_width})
                            else:
                                # Add the part as is for the required quantity.
                                for _ in range(num_pieces):
                                    all_parts.append({'length': part_length, 'width': part_width})
                    
                        E2_1000_board_total = 0
                        if all_parts:
                            # 2. Sort parts by width (descending) to pack more efficiently.
                            all_parts.sort(key=lambda p: p['width'], reverse=True)

                            # 3. Simulate packing parts onto boards using a First-Fit heuristic.
                            boards = [] # This will hold our boards. Each board is a list of its strips/rips.

                            for part in all_parts:
                                part_placed = False
                            
                                # a. Try to fit the part into an existing strip on an existing board.
                                for board in boards:
                                    for strip in board:
                                        # Check if the part width matches the strip and has enough length remaining.
                                        if part['width'] == strip['width'] and (part['length'] + strip['length_used']) <= max_rip_length:
                                            strip['length_used'] += part['length']
                                            part_placed = True
                                            break
                                    if part_placed:
                                        break
                            
                                if part_placed:
                                    continue

                                # b. If not placed, try to add it as a new strip on an existing board.
                                for board in boards:
                                    width_on_board = sum(s['width'] for s in board)
                                    if (part['width'] + width_on_board) <= max_board_width:
                                        # This board has enough width remaining for a new rip.
                                        board.append({'width': part['width'], 'length_used': part['length']})
                                        part_placed = True
                                        break
                            
                                if part_placed:
                                    continue

                                # c. If still not placed, it needs a brand new board.
                                new_board = [{'width': part['width'], 'length_used': part['length']}]
                                boards.append(new_board)

                            # 4. The total number of boards is the number of boards we had to create.
                            E2_1000_board_total = len(boards)
                    
                        
                        if E2_1000_board_total>0:
                            try:
                                # Create a list with the correct number of columns
                                row_data = [""] * len(new_header)
                            
                                # Populate data into the correct columns for the new format
                                row_data[1] = "Board" # BARCODE
                                row_data[2] = MFC_code # MATNAME
                                row_data[3] = 2800 # CLENG
                                row_data[4] = 1000 # CWIDTH
                                row_data[5] = E2_1000_board_total # CNT
                                row_data[8] = grain # GRAIN
                                row_data[10] = row_ref # CUSTOMER
                                row_data[11] = sysNum # ORDERNAME
                                row_data[13] = "Board" # PARTDESC
                                row_data[18] = ":::" # EDGINCORNERSPEC
                                row_data[14] = "Sliderobe_Edge_08" # PRFID1 (L1)
                                row_data[15] = "Sliderobe_Edge_08" # PRFID3 (L2)

                                row_data[35] = "S" # OPTIMISINGPARAM
                                row_data[36] = "S" # SAWPARAM
                                row_data[45] = "Saw,Edging,Dispatch" # ROUTING
                            
                                csvwriter.writerow(row_data)

                            except:
                                print("Failed to write row (E1):")
                                # print the new row_data format for debugging
                                print(row_data)
                
                    tempQuery = "SELECT COUNT(DISTINCT [DIMA]) FROM DISTINTAT where numero = " + str(sysNum) + " and N3 = '" + Colour + "' AND (select reparto from articoli where cod like codcomp ) ='001' and livello=1 and (( lower(N6) like '%e%e%' and lower(N6) NOT IN ('', 'unedged', 'panel') ) or DIMP>18)"
                    BSumCursor.execute(tempQuery)
                    Other_Edged_Count = int(BSumCursor.fetchone()[0])

                    if Other_Edged_Count > 0:
                        # Build Query to select all Other Edged Boards in this job
                        Query = "select (select des from articoli where cod like codcomp ) as [Description], N3 as [Material], DIML as [Length], DIMA as [Width], SUM(QTACOMP) as Quantity, DIMP as [Thickness], "
                        Query = Query + "'' as [Length Edge 1], '' as [Length Edge 2], '' as [Width Edge 1], '' as [Width Edge 2], N6 as [Edging], NUMERO as [SysNum], N5 as [UnitLabel] "
                        Query = Query + "from DISTINTAT a "
                        Query = Query + "where numero = " + str(sysNum) + " and N3 = '" + Colour + "' and (select reparto from articoli where cod like codcomp ) ='001' and livello=1 and (( lower(N6) like '%e%e%' and lower(N6) NOT IN ('', 'unedged', 'panel') ) or DIMP>18)"
                        Query = Query + "group by codcomp,DIML,DIMA,DIMP,N1,N2,N3,N4,N5,N6,numero order by NUMERO, N5, N3 ASC, N1 ASC, DIMA DESC, DIML ASC"
                        BSumCursor.execute(Query)
                        rows = BSumCursor.fetchall()

                        for row in rows:
                            MFC_code = "SHT_MFC_EGG_" + egger_code + st_number + "_" + str(round(row['thickness'])) + "_"
                            # Parse Edging info into individual columns for each long and short side
                            edge_L1 = 0
                            edge_L2 = 0
                            edge_W1 = 0
                            edge_W2 = 0
                            if row['edging'].lower() == "all edges":
                                edge_L1 = 1
                                edge_L2 = 1
                                edge_W1 = 1
                                edge_W2 = 1
                            elif row['edging'].lower() != "unedged" and row['edging'].lower() != "panel":
                                try:
                                    edging_parts = row['edging'].split("E")
                                    for part in edging_parts[1:]:
                                        temp_split = part.strip().split("@")
                                        temp_length = round(float(temp_split[1]), 0)
                                        if temp_length == round(row['length'],0):
                                            if int(temp_split[0]) == 1:
                                                edge_L1 = 1
                                            elif int(temp_split[0]) == 2:
                                                edge_L1 = 1
                                                edge_L2 = 1
                                        elif temp_length == round(row['width'],0):
                                            if int(temp_split[0]) == 1:
                                                edge_W1 = 1
                                            elif int(temp_split[0]) == 2:
                                                edge_W1 = 1
                                                edge_W2 = 1
                                except:
                                    print("Error while parsing edging for this row:")
                                    print(row)
                        
                        
                            try:
                                row_data = [""] * len(new_header)
                            
                                row_data[1] = row['description'] # BARCODE
                                row_data[2] = MFC_code # MATNAME
                                row_data[3] = row['length'] # CLENG
                                row_data[4] = row['width'] # CWIDTH
                                row_data[5] = row['quantity'] # CNT
                                row_data[8] = grain # GRAIN
                                row_data[10] = row_ref # CUSTOMER
                                row_data[11] = row['sysnum'] # ORDERNAME
                                row_data[12] = row['unitlabel'] # ARTICLENAME
                                row_data[13] = row['description'] # PARTDESC
                                row_data[18] = ":::" # EDGINCORNERSPEC
                            
                                # Edging Profile IDs based on parsed edge flags
                                if edge_L1: row_data[14] = "Sliderobe_Edge_08" # PRFID1 (L1)
                                if edge_L2: row_data[15] = "Sliderobe_Edge_08" # PRFID3 (L2)
                                if edge_W1: row_data[16] = "Sliderobe_Edge_08" # PRFID4 (W1)
                                if edge_W2: row_data[17] = "Sliderobe_Edge_08" # PRFID2 (W2)

                                row_data[35] = "S" # OPTIMISINGPARAM
                                row_data[36] = "S" # SAWPARAM
                                row_data[45] = "Saw,Edging,Dispatch" # ROUTING
                            
                                csvwriter.writerow(row_data)
                            
                            except Exception as e:
                                logger.error(f"Failed to write row (Other Edged): {e}")
                                logger.error(f"Row data: {row_data}")
                    
                        logger.info(f"Finished Processing: {row_ref}")

    except Exception as e:
        logger.error(f"Critical error in board order file generation: {e}", exc_info=True)
        raise

    # 5. When done, get the content from the in-memory file
    output_in_memory.seek(0)
    content = output_in_memory.read()
    
    # Count lines (excluding header)
    line_count = content.count('\n') - 1
    logger.info(f"PNX file generation complete: {line_count} data rows generated")
    logger.info(f"File size: {len(content)} bytes")
    
    # Populate the per-system summary for the caller / UI
    if summary is not None:
        rows_per_system = _count_rows_per_system(content)
        total_rows = 0
        systems = []
        for sysNum in numList:
            info = diagnostics.get(sysNum, {
                'system': sysNum, 'total_components': 0, 'board_components': 0,
                'missing_codes': 0, 'board_rows': 0, 'messages': [],
            })
            info['board_rows'] = rows_per_system.get(str(sysNum), 0)
            total_rows += info['board_rows']

            if info['total_components'] == 0:
                info['messages'].append(
                    f"System {sysNum}: not found in the CAD data (0 components). The job may be "
                    f"outside the synced data window or the number may be incorrect."
                )
            elif info['board_components'] == 0:
                info['messages'].append(
                    f"System {sysNum}: has {info['total_components']} component(s) but none are "
                    f"boards (department '001'), so no boards were generated."
                )
            elif info['board_rows'] == 0:
                info['messages'].append(
                    f"System {sysNum}: board components exist but no rows were produced. This is "
                    f"usually caused by colour/edging filters or component codes missing from the "
                    f"'articoli' reference table."
                )

            if info['missing_codes'] > 0:
                info['messages'].append(
                    f"System {sysNum}: {info['missing_codes']} component code(s) are missing from the "
                    f"'articoli' reference table and were skipped - re-sync the CAD data if this is unexpected."
                )

            systems.append(info)
            for m in info['messages']:
                logger.warning(m)

        summary['systems'] = systems
        summary['total_data_rows'] = total_rows

    return content