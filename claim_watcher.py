r"""
Claim PDF Watcher
=================
Monitors C:\evolution\eCadPro\_PDF for new PDF files and automatically
uploads them to the Atlas Claim Service.

Setup:
  1. Install Python 3.8+ on the PC
  2. pip install requests
  3. Edit SERVER_URL and API_KEY below
  4. Run: python claim_watcher.py
  5. (Optional) Set up as a Windows service or scheduled task

The script polls the folder every 30 seconds. It keeps a local record of
uploaded files so it never uploads the same file twice, even after restart.
"""

import os
import sys
import time
import json
import logging
import requests
from pathlib import Path
from datetime import datetime

# ==============================================================================
# CONFIGURATION — Edit these values
# ==============================================================================

# The folder to watch for new PDFs
WATCH_FOLDER = r"C:\evolution\eCadPro\_PDF"

# Your Atlas server URL (no trailing slash)
SERVER_URL = "https://atlas-gxbq5.ondigitalocean.app"

# API key — must match the CLAIM_UPLOAD_API_KEY env var on the server
API_KEY = "diWM84zsbfEZuNHeVpAZJUAbx7877sKZ"

# How often to check for new files (seconds)
POLL_INTERVAL = 30

# Where to store the list of already-uploaded files
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "claim_watcher_state.json")

# ==============================================================================
# LOGGING
# ==============================================================================

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "claim_watcher.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("claim_watcher")

# ==============================================================================
# STATE MANAGEMENT
# ==============================================================================

def load_state():
    """Load the set of already-uploaded filenames."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                return set(data.get("uploaded", []))
        except (json.JSONDecodeError, IOError):
            pass
    return set()


def save_state(uploaded: set):
    """Persist the set of uploaded filenames."""
    with open(STATE_FILE, "w") as f:
        json.dump({"uploaded": sorted(uploaded), "last_updated": datetime.now().isoformat()}, f, indent=2)

# ==============================================================================
# UPLOAD
# ==============================================================================

def upload_pdf(filepath: str) -> bool:
    """Upload a single PDF to the Atlas server. Returns True on success."""
    filename = os.path.basename(filepath)
    url = f"{SERVER_URL}/claims/api/upload/"

    # Extract group_key and customer_name from filename pattern: {number}_{name}_{id}_{type}.PDF
    name_no_ext = os.path.splitext(filename)[0]
    parts = name_no_ext.rsplit('_', 1)
    group_key = parts[0] if len(parts) > 1 else ''
    name_parts = name_no_ext.split('_')
    customer_name = name_parts[1] if len(name_parts) >= 3 else ''

    try:
        with open(filepath, "rb") as f:
            response = requests.post(
                url,
                headers={"X-API-Key": API_KEY},
                files={"file": (filename, f, "application/pdf")},
                data={
                    "title": name_no_ext,
                    "group_key": group_key,
                    "customer_name": customer_name,
                },
                timeout=120,
            )

        if response.status_code == 200:
            result = response.json()
            if result.get("success"):
                logger.info(f"Uploaded: {filename}")
                return True
            elif result.get("skipped"):
                logger.info(f"Skipped (already on server): {filename}")
                return True  # Mark as done so we don't retry
            else:
                logger.warning(f"Server rejected {filename}: {result}")
                return False
        else:
            logger.error(f"Upload failed for {filename}: HTTP {response.status_code} - {response.text[:200]}")
            return False

    except requests.ConnectionError:
        logger.error(f"Connection error uploading {filename} — is the server reachable?")
        return False
    except requests.Timeout:
        logger.error(f"Timeout uploading {filename}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error uploading {filename}: {e}")
        return False

# ==============================================================================
# WATCHER LOOP
# ==============================================================================

def get_pdf_files(folder: str) -> list:
    """Get all PDF files in the folder (non-recursive)."""
    try:
        return [
            os.path.join(folder, f)
            for f in os.listdir(folder)
            if f.lower().endswith(".pdf") and os.path.isfile(os.path.join(folder, f))
        ]
    except FileNotFoundError:
        logger.error(f"Watch folder not found: {folder}")
        return []
    except PermissionError:
        logger.error(f"Permission denied accessing: {folder}")
        return []


def run_watcher():
    """Main watcher loop."""
    logger.info("=" * 60)
    logger.info("Claim PDF Watcher starting")
    logger.info(f"  Watch folder : {WATCH_FOLDER}")
    logger.info(f"  Server       : {SERVER_URL}")
    logger.info(f"  Poll interval: {POLL_INTERVAL}s")
    logger.info("=" * 60)

    if not os.path.isdir(WATCH_FOLDER):
        logger.error(f"Watch folder does not exist: {WATCH_FOLDER}")
        logger.error("Please check the WATCH_FOLDER path and try again.")
        sys.exit(1)

    uploaded = load_state()
    logger.info(f"Loaded state: {len(uploaded)} files previously uploaded")

    # Initial sync — upload everything not yet uploaded
    pdf_files = get_pdf_files(WATCH_FOLDER)
    new_files = [f for f in pdf_files if os.path.basename(f) not in uploaded]

    if new_files:
        logger.info(f"Found {len(new_files)} new file(s) to upload on startup")
        for filepath in sorted(new_files):
            if upload_pdf(filepath):
                uploaded.add(os.path.basename(filepath))
                save_state(uploaded)
            time.sleep(1)  # Small delay between uploads

    logger.info("Watching for new files... (Ctrl+C to stop)")

    try:
        while True:
            time.sleep(POLL_INTERVAL)

            pdf_files = get_pdf_files(WATCH_FOLDER)
            new_files = [f for f in pdf_files if os.path.basename(f) not in uploaded]

            for filepath in sorted(new_files):
                # Wait a moment to ensure the file is fully written
                try:
                    size1 = os.path.getsize(filepath)
                    time.sleep(2)
                    size2 = os.path.getsize(filepath)
                    if size1 != size2:
                        logger.info(f"File still being written, skipping for now: {os.path.basename(filepath)}")
                        continue
                except OSError:
                    continue

                if upload_pdf(filepath):
                    uploaded.add(os.path.basename(filepath))
                    save_state(uploaded)
                time.sleep(1)

    except KeyboardInterrupt:
        logger.info("Watcher stopped by user")
        save_state(uploaded)


if __name__ == "__main__":
    run_watcher()
