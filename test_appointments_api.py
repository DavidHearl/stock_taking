"""Quick test: dump all appointment fields from GetSaleAppointments for known sales."""
import os, sys, xml.etree.ElementTree as ET
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'stock_taking.settings')
import django; django.setup()

import requests

USERNAME = os.getenv('ANTHILL_USERNAME')
PASSWORD = os.getenv('ANTHILL_PASSWORD')
NAMESPACE = 'http://www.anthill.co.uk/'
BASE_URL = 'https://sliderobes.anthillcrm.com/api/v1.asmx'

SALE_IDS = ['418452', '417437', '419324', '418405', '418230']


def get_appts(sale_id):
    body = f'<GetSaleAppointments xmlns="{NAMESPACE}"><saleId>{sale_id}</saleId></GetSaleAppointments>'
    envelope = (
        f'<?xml version="1.0" encoding="utf-8"?>'
        f'<soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
        f' xmlns:xsd="http://www.w3.org/2001/XMLSchema"'
        f' xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">'
        f'<soap12:Header><AuthHeader xmlns="{NAMESPACE}">'
        f'<Username>{USERNAME}</Username><Password>{PASSWORD}</Password>'
        f'</AuthHeader></soap12:Header>'
        f'<soap12:Body>{body}</soap12:Body></soap12:Envelope>'
    )
    resp = requests.post(
        BASE_URL, data=envelope.encode('utf-8'),
        headers={'Content-Type': 'application/soap+xml; charset=utf-8',
                 'SOAPAction': f'{NAMESPACE}GetSaleAppointments'},
        timeout=30,
    )
    print(f'HTTP {resp.status_code}')
    root = ET.fromstring(resp.text)
    appts = root.findall(f'.//{{{NAMESPACE}}}Appointment')
    print(f'Appointments found: {len(appts)}')
    for i, a in enumerate(appts):
        print(f'  --- Appointment {i+1} ---')
        for c in a:
            tag = c.tag.replace(f'{{{NAMESPACE}}}', '')
            print(f'    {tag}: {(c.text or "").strip()[:80]}')
    if not appts:
        # Show raw XML so we can see what IS in the response
        print('  Raw response (snippet):')
        print('  ', resp.text[200:600])


for sid in SALE_IDS:
    print(f'\n=== Sale {sid} ===')
    get_appts(sid)

print('\nDone.')
