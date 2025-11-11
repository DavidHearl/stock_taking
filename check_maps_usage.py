#!/usr/bin/env python
"""
Google Maps Usage Monitor
=========================

This script helps monitor your Google Maps Platform API usage to avoid unexpected charges.

Usage:
    python check_maps_usage.py

Or as a Django management command:
    python manage.py check_maps_usage

Setup Instructions:
==================

1. Google Cloud Console Monitoring:
   - Visit: https://console.cloud.google.com/apis/dashboard
   - Select your project
   - View real-time usage under "API Dashboard"

2. Set up Billing Alerts:
   - Go to: https://console.cloud.google.com/billing
   - Select your billing account
   - Click "Budgets & alerts"
   - Create a budget with alerts at 50%, 80%, and 100% of your expected usage

3. Automated Monitoring:
   - Run the Django management command weekly
   - Set up cron job: 0 9 * * 1 python manage.py check_maps_usage

Free Tier Limits (as of 2025):
============================
- Dynamic Maps: 10,000 loads/month
- Geocoding: 10,000 requests/month
- Static Maps: 10,000 requests/month

For your stock-taking app, typical usage should stay well under these limits.
"""

import os
import sys
import django

# Setup Django
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'stock_taking.settings')
django.setup()

from django.core.management import execute_from_command_line

if __name__ == '__main__':
    execute_from_command_line(['manage.py', 'check_maps_usage'])