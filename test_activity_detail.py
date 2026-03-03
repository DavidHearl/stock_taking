#!/usr/bin/env python
"""Discover Anthill API methods: try GetActivityDetails and dump all child XML."""
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

def soap_request(action, body_xml):
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
    {body_xml}
  </soap12:Body>
</soap12:Envelope>'''
    resp = requests.post(BASE_URL, data=envelope.encode('utf-8'),
        headers={'Content-Type': 'application/soap+xml; charset=utf-8',
                 'SOAPAction': f'{NAMESPACE}{action}'}, timeout=30)
    return resp

def print_all_children(node, indent=0):
    """Recursively print all XML children."""
    prefix = '  ' * indent
    tag = node.tag.replace(f'{{{NAMESPACE}}}', '')
    text = (node.text or '').strip()
    if text:
        print(f'{prefix}{tag}: {text[:120]}')
    else:
        print(f'{prefix}{tag}:')
    for child in node:
        print_all_children(child, indent + 1)

# Get a recent sale activity
sale = AnthillSale.objects.filter(activity_type__icontains='sale').exclude(anthill_activity_id='').first()
if not sale:
    sale = AnthillSale.objects.exclude(anthill_activity_id='').first()
    
act_id = sale.anthill_activity_id
print(f'Testing with activity {act_id} ({sale.customer_name}, type={sale.activity_type}, status={sale.status})')
print()

# Try GetActivityDetails
print('=' * 60)
print('Trying: GetActivityDetails')
print('=' * 60)
body = f'''<GetActivityDetails xmlns="{NAMESPACE}">
  <activityId>{act_id}</activityId>
</GetActivityDetails>'''
resp = soap_request('GetActivityDetails', body)
print(f'HTTP {resp.status_code}')
if resp.status_code == 200:
    root = ET.fromstring(resp.text)
    result = root.find(f'.//{{{NAMESPACE}}}GetActivityDetailsResult')
    if result is not None:
        print('FOUND GetActivityDetailsResult:')
        print_all_children(result)
    else:
        # Print the full body for debugging
        body_el = root.find('.//{http://www.w3.org/2003/05/soap-envelope}Body')
        if body_el is not None:
            print('Body:')
            print_all_children(body_el)
        else:
            print(resp.text[:3000])
else:
    print(resp.text[:2000])

print()

# Also try GetWorkflowStatus
print('=' * 60)
print('Trying: GetWorkflowStatus')
print('=' * 60)
body2 = f'''<GetWorkflowStatus xmlns="{NAMESPACE}">
  <activityId>{act_id}</activityId>
</GetWorkflowStatus>'''
resp2 = soap_request('GetWorkflowStatus', body2)
print(f'HTTP {resp2.status_code}')
if resp2.status_code == 200:
    root2 = ET.fromstring(resp2.text)
    body_el = root2.find('.//{http://www.w3.org/2003/05/soap-envelope}Body')
    if body_el is not None:
        print_all_children(body_el)
    else:
        print(resp2.text[:3000])
else:
    print(resp2.text[:2000])
