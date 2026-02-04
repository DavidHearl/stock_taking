import win32com.client
import time
import os
import re
from datetime import datetime

# --- Configuration ---
WATCH_FOLDER_NAME = "Olympus"
PDF_DIR = r"C:\evolution\eCadPro\_PDF"

# Regex to find the specific claim pattern: 2-6 digits, then name, optionally another 2-6 digits
# Examples: 409546_Andrew Reynolds-Forsyth, 1856_Urwin_35205, 17_Butler_1234
CLAIM_PATTERN = re.compile(r'(\d{2,6})_([A-Za-z\s\-]+)(?:_(\d{2,6}))?')
# Fallback for simple 4-6 digit numbers (keep at 4+ to avoid false positives on random 2-digit numbers)
SIMPLE_NUM = re.compile(r'\b(\d{4,6})\b')
# Phone number patterns to exclude (UK mobile numbers are 11 digits: 07 + 9 digits)
PHONE_PATTERNS = [
    r'\b07\d{9}\b',                    # UK mobile: 07594651632
    r'\b07\d{3}\s*\d{3}\s*\d{3}\b',    # UK mobile with spaces: 07535 320 422
    r'\b02\d{8,9}\b',                  # UK landline
    r'\bP:\s*\d+',                     # P: followed by numbers
    r'\bT:\s*\d+',                     # T: followed by numbers
]

def score_filename_match(filename, identifiers, has_name=False):
    """
    Score how well a filename matches the given identifiers.
    Higher score = better match.
    Prioritizes exact word-boundary matches over substring matches.
    Gives bonus for matching the name when available.
    """
    score = 0
    matches_count = 0
    filename_upper = filename.upper()
    
    for identifier in identifiers:
        identifier_upper = str(identifier).upper()
        
        # Check for exact word boundary match (highest score)
        # Pattern: identifier surrounded by non-alphanumeric chars or start/end
        if re.search(rf'(?:^|[^A-Z0-9]){re.escape(identifier_upper)}(?:[^A-Z0-9]|$)', filename_upper):
            score += 10
            matches_count += 1
        # Substring match (lower score, can cause false positives)
        elif identifier_upper in filename_upper:
            score += 1
    
    # Bonus for matching more identifiers (prefer files that match multiple criteria)
    score += matches_count * 5
    
    # Big bonus if the filename matches the name (when provided)
    if has_name and len(identifiers) > 0:
        # The last identifier in the list is the name when has_name is True
        name_upper = str(identifiers[-1]).upper()
        if re.search(rf'(?:^|[^A-Z0-9]){re.escape(name_upper)}(?:[^A-Z0-9]|$)', filename_upper):
            score += 20  # Big bonus for name match
    
    return score

def get_job_files(identifiers):
    """
    Checks the directory for the 3 specific suffixes.
    identifiers: dict with 'numbers' list and optional 'name' string
    Returns the best matching file set based on scoring.
    If multiple files match, selects the one with the highest 02XXXX number.
    """
    suffixes = [
        "_A3ProdnDrawings.PDF", 
        "_BillOfMaterials.PDF", 
        "_ProductionDrawings.PDF"
    ]
    
    all_files = os.listdir(PDF_DIR)
    
    # Build list of all identifiers to match
    all_identifiers = identifiers.get('numbers', [])
    has_name = False
    if identifiers.get('name'):
        all_identifiers.append(identifiers['name'])
        has_name = True
    
    # Track candidates: key is a normalized identifier, value is (score, match_count, file_set, display_id)
    candidates = {}
    
    # If we have a name, also search by name alone (in case numbers don't match)
    search_criteria = identifiers.get('numbers', [])[:]
    if identifiers.get('name'):
        search_criteria.append(identifiers['name'])
    
    # Try each search criterion as potential job identifier
    for criterion in search_criteria:
        # Skip if it looks like a phone number
        if isinstance(criterion, str) and criterion.isdigit() and (criterion.startswith("07") or criterion.startswith("02")):
            continue
        
        # Find files for this criterion
        file_matches = {}
        for sfx in suffixes:
            matching_files = [f for f in all_files if f.upper().endswith(sfx.upper())]
            
            # Score each matching file and track all candidates
            candidate_files = []
            for f in matching_files:
                # For names, do a more flexible match; for numbers, use word boundaries
                if isinstance(criterion, str) and not criterion.isdigit():
                    # Name search - check if name appears in filename (remove special chars for matching)
                    clean_criterion = re.sub(r"[^A-Z0-9]", "", str(criterion).upper())
                    clean_filename = re.sub(r"[^A-Z0-9_]", "", f.upper())
                    if clean_criterion in clean_filename:
                        score = score_filename_match(f, all_identifiers, has_name)
                        if score > 0:
                            candidate_files.append((f, score))
                else:
                    # Number search - check if the number appears in the filename with word boundaries
                    # This prevents "1861" from matching "021861"
                    if re.search(rf'(?:^|[^A-Z0-9]){re.escape(str(criterion))}(?:[^A-Z0-9]|$)', f.upper()):
                        score = score_filename_match(f, all_identifiers, has_name)
                        if score > 0:
                            candidate_files.append((f, score))
            
            # If we have candidates, select the best one
            if candidate_files:
                # Sort by score first, then by 02XXXX number (extract and compare)
                def get_job_number(filename):
                    """Extract the 02XXXX job number from filename"""
                    match = re.search(r'_0(2\d{4,5})_', filename)
                    return int(match.group(1)) if match else 0
                
                # Sort: highest score first, then highest job number
                candidate_files.sort(key=lambda x: (x[1], get_job_number(x[0])), reverse=True)
                best_file, best_score = candidate_files[0]
                file_matches[sfx] = (best_file, best_score)
        
        # If we found all 3 files, calculate total score
        if len(file_matches) == 3:
            total_score = sum(score for _, score in file_matches.values())
            file_set = [os.path.join(PDF_DIR, f) for f, _ in file_matches.values()]
            
            # Count how many identifiers matched (for tie-breaking)
            # Use the first file to check match count
            sample_file = list(file_matches.values())[0][0]
            match_count = sum(1 for ident in all_identifiers 
                            if re.search(rf'(?:^|[^A-Z0-9]){re.escape(str(ident).upper())}(?:[^A-Z0-9]|$)', sample_file.upper()))
            
            # Use the criterion as key (avoid duplicates)
            # Prefer candidates with higher scores, then higher match counts
            criterion_key = str(criterion)
            if criterion_key not in candidates or total_score > candidates[criterion_key][0] or \
               (total_score == candidates[criterion_key][0] and match_count > candidates[criterion_key][1]):
                candidates[criterion_key] = (total_score, match_count, file_set, criterion_key)
    
    # Return the candidate with the highest score and match count
    if candidates:
        best_match = max(candidates.values(), key=lambda x: (x[0], x[1]))
        return best_match[2], best_match[3]
    
    return [], None

def process_claims():
    outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
    olympus_inbox = None

    for folder in outlook.Folders:
        if folder.Name == WATCH_FOLDER_NAME:
            olympus_inbox = folder.Folders["Inbox"]
            break

    if not olympus_inbox: return

    messages = olympus_inbox.Items
    messages.Sort("[ReceivedTime]", True)

    for message in messages:
        try:
            # Check for Reply (102)
            if message.PropertyAccessor.GetProperty("http://schemas.microsoft.com/mapi/proptag/0x10810003") == 102:
                continue 

            # Only search the subject line and first part of body (before signature)
            # Split body at common signature markers
            body_text = message.Body
            signature_markers = [
                '\n\nDavid Hearl', '\nAlex Duguid', '\nDirector', 
                '\nChief Information Officer', '\nP:', '\nE:', 
                'Sliderobes.com', 'sliderobes.com'
            ]
            for marker in signature_markers:
                if marker in body_text:
                    body_text = body_text.split(marker)[0]
                    break
            
            text_to_search = f"{message.Subject} {body_text}"
            
            # Dictionary to store extracted identifiers
            identifiers = {'numbers': [], 'name': None}
            
            # Try to find the exact pattern: number_name_number (e.g., 1856_Urwin_35205)
            complex_match = CLAIM_PATTERN.search(text_to_search)
            if complex_match:
                # Extract all components
                identifiers['numbers'].append(complex_match.group(1))  # First number
                identifiers['name'] = complex_match.group(2).strip()   # Name
                if complex_match.group(3):
                    identifiers['numbers'].append(complex_match.group(3))  # Second number
            else:
                # Fallback to any 4-6 digit numbers found (but filter out phone numbers)
                all_nums = SIMPLE_NUM.findall(text_to_search)
                # Filter out numbers that appear in phone number context
                for num in all_nums:
                    # Check if this number is part of a phone pattern
                    is_phone = False
                    for phone_pattern in PHONE_PATTERNS:
                        if re.search(phone_pattern.replace(r'\d+', num), text_to_search):
                            is_phone = True
                            break
                    if not is_phone and not num.startswith("07"):
                        identifiers['numbers'].append(num)

            if identifiers['numbers'] or identifiers['name']:
                # Remove duplicate numbers from the list
                identifiers['numbers'] = list(dict.fromkeys(identifiers['numbers']))
                
                timestamp = datetime.now().strftime("%H:%M:%S")
                # Build compact search description
                search_parts = identifiers['numbers'][:]
                if identifiers['name']:
                    search_parts.append(identifiers['name'])
                search_desc = '_'.join(str(p) for p in search_parts)
                
                pdf_set, matched_id = get_job_files(identifiers)
                
                if pdf_set:
                    print(f"[{timestamp}] ✓ {search_desc} → Sending reply ({message.SenderName})")
                    reply = message.Reply()
                    reply.Body = f"Hi,\n\nPlease find the 3 drawings for Job {matched_id} attached.\n\nRegards,\nOlympus Print Server\n\n---\nThis is an automated email."
                    for pdf in pdf_set:
                        reply.Attachments.Add(pdf)
                    reply.Send()
                else:
                    print(f"[{timestamp}] ✗ {search_desc} → Not found ({message.SenderName})")

        except Exception as e:
            print(f"Processing Error: {e}")

if __name__ == "__main__":
    print("Olympus Monitor Active (Pattern Match Mode)...")
    while True:
        process_claims()
        time.sleep(300)