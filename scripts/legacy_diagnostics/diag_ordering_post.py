import os, django, traceback, sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'stock_taking.settings')
django.setup()

from django.test import Client
from django.contrib.auth.models import User

# Raise actual exceptions instead of returning 500 response
client = Client(raise_request_exception=True)
user = User.objects.filter(is_superuser=True).first()
if not user:
    print("No superuser found")
    sys.exit(1)

client.force_login(user)
print(f'Logged in as: {user.username}')

import django.test.utils
django.test.utils.setup_test_environment()

# Try all POST scenarios
scenarios = [
    ('price_per_sqm update', {'price_per_sqm': '15.00'}),
    ('invalid form (missing fields)', {'first_name': 'Test', 'last_name': 'User', 'sale_number': '123456', 'customer_number': '012345', 'order_type': 'sale'}),
    ('valid form', {'first_name': 'TestDelete', 'last_name': 'UserDelete', 'sale_number': '999999', 'customer_number': '099999', 'order_type': 'sale', 'total_value_inc_vat': '1000.00'}),
]

from stock_take.models import Order

for name, data in scenarios:
    try:
        response = client.post('/ordering/', data=data)
        print(f'[{name}] Status: {response.status_code}')
        if response.status_code == 500:
            print(f'  GOT 500!')
            import django.test.signals
            # Get exception info
            if hasattr(response, 'exc_info') and response.exc_info:
                import traceback as tb2
                tb2.print_exception(*response.exc_info)
    except Exception as e:
        print(f'[{name}] EXCEPTION: {e}')
        traceback.print_exc()

# Clean up
Order.objects.filter(sale_number='999999').delete()
