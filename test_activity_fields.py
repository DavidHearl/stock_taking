#!/usr/bin/env python
"""Quick one-off script to dump all XML fields in Anthill RecentActivityModel."""
import os, sys, xml.etree.ElementTree as ET
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
load_dotenv(os.path.join(BASE_DIR, '.env'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'stock_taking.settings')

import django; django.setup()

import requests
from stock_take.models import AnthillSale

USERNAME = os.getenv('ANTHILL_USERNAME')
PASSWORD = os.getenv('ANTHILL_PASSWORD')
NAMESPACE = 'http://www.anthill.co.uk/'
BASE_URL = 'https://sliderobes.anthillcrm.com/api/v1.asmx'

# Pick a known customer
sale = AnthillSale.objects.exclude(anthill_customer_id='').first()
cust_id = sale.anthill_customer_id
print(f'Testing customer {cust_id} ({sale.customer_name})')

envelope = f'''<?xml version="1.0" encoding="utf-8"?>
<soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                 xmlns:xsd="http://www.w3.org/2001/XMLSchema"
                 xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
  <soap12:Header>
    <AuthHeader xmlns="{NAMESPACE}">
      <Username>{USERNAME}</Username>
      <Password>{PASSWORD}</Password>
    </AuthHeader>
  </soap12:Header>
  <soap12:Body>
    <GetCustomerDetails xmlns="{NAMESPACE}">
      <customerId>{cust_id}</customerId>
      <includeActivity>true</includeActivity>
    </GetCustomerDetails>
  </soap12:Body>
</soap12:Envelope>'''

resp = requests.post(BASE_URL, data=envelope.encode('utf-8'),
    headers={
        'Content-Type': 'application/soap+xml; charset=utf-8',
        'SOAPAction': f'{NAMESPACE}GetCustomerDetails',
    }, timeout=30)

print(f'HTTP {resp.status_code}')

root = ET.fromstring(resp.text)
for i, act in enumerate(root.findall(f'.//{{{NAMESPACE}}}RecentActivityModel')):
    print(f'\n--- Activity {i+1} ---')
    for child in act:
        tag = child.tag.replace(f'{{{NAMESPACE}}}', '')
        text = (child.text or '').strip()[:120]
        print(f'  {tag}: {text}')

print('\nDone.')
