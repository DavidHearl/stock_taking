#!/usr/bin/env python
"""
test_anthill_payments_screen.py
────────────────────────────────
Scrape the Anthill CRM "Payments" screen and print the first rows.

Uses Playwright (same pattern as scrape_anthill_orders_to_place in views.py)
to navigate to the JS-rendered payments screen, parse the table, and print
a summary.

Usage:
    python test_anthill_payments_screen.py
    python test_anthill_payments_screen.py --max-rows 20
"""

import os
import re
import sys
import argparse
from html.parser import HTMLParser
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))


HEADERS = [
    'Showroom', 'Year Month', 'Payment Date', 'Customer', 'Payment Type',
    'Amount Paid', 'Created By', 'LastUpdated', 'Payment Status',
    'Contract Number', 'Method', 'Payment Received',
    'IFC Provider', 'UK Terms', 'ROI Terms',
]


class PaymentsTableParser(HTMLParser):
    """Extract rows from the first <table class="sortable"> inside component-1."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._in_target = False
        self._in_tbody = False
        self._depth = 0
        self._in_row = False
        self._in_cell = False
        self._current_row = []
        self._current_cell_parts = []
        self.rows = []
        self.found = False

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        if tag == 'table' and not self._in_target:
            if 'sortable' in attr_dict.get('class', '').split():
                self._in_target = True
                self._depth = 1
                return
        if not self._in_target:
            return
        if tag == 'table':
            self._depth += 1
        elif tag == 'tbody':
            self._in_tbody = True
        elif tag == 'tr' and self._in_tbody:
            self._in_row = True
            self._current_row = []
        elif tag in ('td', 'th') and self._in_row:
            self._in_cell = True
            self._current_cell_parts = []

    def handle_endtag(self, tag):
        if not self._in_target:
            return
        if tag == 'table':
            self._depth -= 1
            if self._depth == 0:
                self._in_target = False
                self.found = True
        elif tag == 'tbody':
            self._in_tbody = False
        elif tag == 'tr' and self._in_row:
            self._in_row = False
            self.rows.append(list(self._current_row))
        elif tag in ('td', 'th') and self._in_cell:
            self._in_cell = False
            text = re.sub(r'\s+', ' ', ' '.join(self._current_cell_parts)).strip()
            self._current_row.append(text)

    def handle_data(self, data):
        if self._in_cell:
            stripped = data.strip()
            if stripped:
                self._current_cell_parts.append(stripped)


def scrape_payments(max_rows=10):
    username = os.getenv('ANTHILL_USER_USERNAME')
    password = os.getenv('ANTHILL_USER_PASSWORD')
    subdomain = os.getenv('ANTHILL_SUBDOMAIN', 'sliderobes')

    if not username or not password:
        print('ERROR: ANTHILL_USER_USERNAME / ANTHILL_USER_PASSWORD not set in .env')
        sys.exit(1)

    base_url = f'https://{subdomain}.anthillcrm.com'
    target_url = f'{base_url}/n/screens/12/CAIaEgmLsAFMrjHYQhGCvnwKumuXYyiDAw'

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print('ERROR: Playwright not installed. Run: pip install playwright && playwright install chromium')
        sys.exit(1)

    print(f'Scraping payments from: {target_url}')
    print(f'Logging in as: {username}')
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )
        page = context.new_page()

        # Navigate to payments screen — Anthill will redirect to login
        page.goto(target_url, timeout=30000)
        page.wait_for_load_state('domcontentloaded', timeout=15000)

        # Handle login if redirected
        current_url = page.url.lower()
        if 'sign-in' in current_url or 'login' in current_url or 'signin' in current_url:
            print('Redirected to login page, authenticating...')

            all_inputs = page.locator('input:visible').all()
            text_inputs = []
            pass_inputs = []
            for inp in all_inputs:
                try:
                    itype = (inp.get_attribute('type') or 'text').lower()
                    if itype == 'password':
                        pass_inputs.append(inp)
                    elif itype in ('text', 'email', ''):
                        text_inputs.append(inp)
                except Exception:
                    pass

            if text_inputs:
                text_inputs[0].fill(username)
            if pass_inputs:
                pass_inputs[0].fill(password)

            submit = page.locator('button[type="submit"], input[type="submit"]').first
            submit.click()

            page.wait_for_load_state('networkidle', timeout=30000)
            print(f'Post-login URL: {page.url}')

            # Navigate to target if not already there
            if '/n/screens/12/' not in page.url:
                page.goto(target_url, timeout=30000)
                page.wait_for_load_state('networkidle', timeout=30000)

        # Wait for the payments table to render
        print('Waiting for payments table...')
        try:
            page.wait_for_selector('table.sortable tbody tr', timeout=30000)
        except Exception:
            print('WARNING: Timed out waiting for table rows. Trying to parse anyway.')

        html_content = page.content()
        browser.close()

    # Parse the HTML
    parser = PaymentsTableParser()
    parser.feed(html_content)

    if not parser.found:
        print('ERROR: Could not find the payments table in the page HTML.')
        # Dump a snippet for debugging
        print(f'Page length: {len(html_content)} chars')
        print(html_content[:2000])
        return

    rows = parser.rows
    print(f'Found {len(rows)} payment rows on page 1')
    print()

    # Print as a formatted table
    display_rows = rows[:max_rows]

    # Use short headers for display
    short_headers = ['Showroom', 'Date', 'Customer', 'Type', 'Amount', 'Status', 'Contract', 'Method']
    col_indices = [0, 2, 3, 4, 5, 8, 9, 10]  # indices into the full row

    # Calculate column widths
    col_widths = [len(h) for h in short_headers]
    for row in display_rows:
        for i, idx in enumerate(col_indices):
            if idx < len(row):
                col_widths[i] = max(col_widths[i], len(row[idx]))

    # Cap widths
    col_widths = [min(w, 25) for w in col_widths]

    # Print header
    header_line = ' | '.join(h.ljust(col_widths[i]) for i, h in enumerate(short_headers))
    print(header_line)
    print('-' * len(header_line))

    # Print rows
    for row in display_rows:
        cells = []
        for i, idx in enumerate(col_indices):
            val = row[idx] if idx < len(row) else ''
            cells.append(val[:col_widths[i]].ljust(col_widths[i]))
        print(' | '.join(cells))

    if len(rows) > max_rows:
        print(f'\n... and {len(rows) - max_rows} more rows')

    # Print all fields for first row as reference
    print('\n\n── Full field dump for first row ──')
    if rows:
        for i, header in enumerate(HEADERS):
            val = rows[0][i] if i < len(rows[0]) else '(missing)'
            print(f'  {header:20s}: {val}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Scrape Anthill CRM payments screen')
    parser.add_argument('--max-rows', type=int, default=10, help='Max rows to display')
    args = parser.parse_args()
    scrape_payments(max_rows=args.max_rows)
