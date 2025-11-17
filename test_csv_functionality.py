#!/usr/bin/env python
import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'stock_taking.settings')
sys.path.insert(0, os.path.dirname(__file__))
django.setup()

from django.test import RequestFactory
from django.contrib.auth.models import User
from stock_take.views import upload_accessories_csv
from django.contrib import messages
from stock_take.models import Order

# Get an existing order
order = Order.objects.filter(customer_number='020483').first()
if not order:
    print("No order found with customer_number 020483")
    sys.exit(1)

print(f"Testing with order: {order.customer_number} - {order.first_name} {order.last_name}")

# Create test CSV file content
csv_content = """Sku,Name,CostPrice,SellPrice,Quantity,Billable,Description
MISSING123,Missing Test Item,10.50,15.75,2,TRUE,This item should be substituted
EXISTING456,Existing Test Item,5.25,8.99,1,TRUE,This item should work normally
MISSING789,Another Missing Item,20.00,30.00,1,TRUE,This should create a new substitution"""

# Create a mock file
from io import BytesIO
csv_file = BytesIO(csv_content.encode('utf-8'))
csv_file.name = f'{order.customer_number}_test_accessories.csv'

# Create mock request
factory = RequestFactory()
request = factory.post('/upload-accessories-csv/', {'csv_file': csv_file})

# Create or get a user
try:
    user = User.objects.get(username='testuser')
except User.DoesNotExist:
    user = User.objects.create_user('testuser', 'test@example.com', 'password')
request.user = user

print("Processing CSV upload...")
try:
    response = upload_accessories_csv(request)
    print(f"Response status: {response.status_code}")
    print("CSV processing completed successfully!")
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()