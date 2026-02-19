# Stock Taking Application

A Django-based stock taking application with order management and location mapping.

## Features

- Order management with customer details
- BoardsPO file processing
- PNX item tracking
- Interactive map showing all order locations simultaneously
- Northern Ireland focused mapping

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Run migrations:
   ```bash
   python manage.py migrate
   ```

3. Create superuser:
   ```bash
   python manage.py createsuperuser
   ```

4. Run the development server:
   ```bash
   python manage.py runserver
   ```

## Google Maps API Setup

The map functionality requires a Google Maps API key:

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Enable the following APIs:
   - Maps JavaScript API
   - Geocoding API
4. Create credentials (API Key)
5. In `stock_take/templates/stock_take/map.html`, replace `YOUR_API_KEY_HERE` with your actual API key:
   ```javascript
   script.src = 'https://maps.googleapis.com/maps/api/js?key=YOUR_ACTUAL_API_KEY&callback=initMap';
   ```

## Google Maps Usage Monitoring

This application includes built-in monitoring to help you stay within Google Maps Platform free tier limits and avoid unexpected charges.

### Free Tier Limits (2025)
- **Dynamic Maps**: 10,000 loads/month
- **Geocoding**: 10,000 requests/month
- **Static Maps**: 10,000 requests/month

### Monitoring Commands

Check your current usage with the Django management command:

```bash
python manage.py check_maps_usage
```

This will show:
- Current usage vs free tier limits
- Alert warnings when approaching limits
- Recommendations for monitoring setup

### Setting Up Alerts (Important!)

1. **Google Cloud Billing Alerts** (Most Critical):
   - Visit: https://console.cloud.google.com/billing
   - Select your billing account
   - Click "Budgets & alerts"
   - Create a budget with alerts at 50%, 80%, and 100% of your expected usage
   - Set a low budget amount (e.g., $10/month) to catch any unexpected usage

2. **Real-time Usage Dashboard**:
   - Visit: https://console.cloud.google.com/apis/dashboard
   - Monitor API usage in real-time
   - Set up custom alerts for specific APIs

3. **Weekly Monitoring**:
   ```bash
   # Run this command weekly to check usage
   python manage.py check_maps_usage
   ```

### Automated Monitoring Setup

For automated weekly checks:

**Windows Task Scheduler**:
1. Open Task Scheduler
2. Create new task → Weekly trigger (Monday 9:00 AM)
3. Action: Start a program
4. Program: `python`
5. Arguments: `manage.py check_maps_usage`
6. Start in: `C:\path\to\your\stock_taking`

**Linux/Mac (crontab)**:
```bash
# Edit crontab
crontab -e

# Add this line for weekly Monday 9 AM checks
0 9 * * 1 cd /path/to/stock_taking && python manage.py check_maps_usage >> maps_usage.log 2>&1
```

### Usage Optimization Tips

- **Caching**: Geocoding results are cached to minimize API calls
- **Monitoring**: Regular usage checks prevent surprise charges
- **Alerts**: Set conservative budget alerts ($10/month recommended)
- **Review**: Check Google Cloud Console monthly for detailed usage

### Current Usage Status

For a typical small stock-taking application:
- **Expected Usage**: Well under 1% of free tier limits
- **Cost**: $0/month (stays within free tier)
- **Monitoring**: Essential to prevent unexpected charges

## Usage

- Access the application at `http://localhost:8000`
- Use the Map page to view all order locations simultaneously
- Click on map markers to see order details
- Use "Fit All" to show all locations, "Reset View" to return to Northern Ireland overview

---

## Anthill CRM Sync

Import customers and sales data from Anthill CRM into the local database.

### Standalone Script

```bash
# Full sync (customers + sales)
python sync_anthill_customers.py

# Only sync sales for existing customers
python sync_anthill_customers.py --sales-only

# Only import new customers (skip sales phase)
python sync_anthill_customers.py --skip-sales

# Import last 365 days only
python sync_anthill_customers.py --days 365

# Dry run — preview what would be synced without saving
python sync_anthill_customers.py --dry-run
```

### Management Command

```bash
# Full sync with customer details
python manage.py sync_anthill_customers

# Dry run
python manage.py sync_anthill_customers --dry-run

# Limit to first 100 customers
python manage.py sync_anthill_customers --limit 100

# Skip fetching full customer details (faster)
python manage.py sync_anthill_customers --skip-details
```

---

## Xero Integration

Match local customers to Xero contacts and fetch invoice payment data.

> **Prerequisite:** You must first connect to Xero via the **Xero Status** page
> in the web app (`/xero/status/`). This establishes the OAuth2 token needed for
> all API calls.

### Sync Customers to Xero

Fetches all contacts from Xero and matches them to local Customer records by
name. Stores the Xero Contact ID on each matched customer.

```bash
# Full customer sync
python manage.py sync_xero_customers

# Dry run — see what would be matched without saving
python manage.py sync_xero_customers --dry-run
```

### Sync Invoices from Xero

For every customer with a confirmed Xero Contact ID, fetches their invoices and
updates (or creates) local Invoice records with payment status.

```bash
# Full invoice sync
python manage.py sync_xero_invoices

# Dry run — preview without saving
python manage.py sync_xero_invoices --dry-run

# Sync invoices for a single customer (by database PK)
python manage.py sync_xero_invoices --customer 123
```

---

## Colour Pallet
Background Colour #272831 - 39, 40, 49
Accent Colour #32323b - 50, 50, 59
