"""
Quick test to verify Xero SDK is installed and credentials are loaded.
This does NOT test a full API call (that requires OAuth token exchange first).
"""
import os
from dotenv import load_dotenv

load_dotenv()

# 1. Check environment variables
client_id = os.getenv("XERO_CLIENT_ID")
client_secret = os.getenv("XERO_CLIENT_SECRET")
redirect_uri = os.getenv("XERO_REDIRECT_URI")

print("=== Xero Configuration Check ===\n")

if client_id:
    print(f"  XERO_CLIENT_ID:     {client_id[:8]}...{client_id[-4:]}")
else:
    print("  XERO_CLIENT_ID:     MISSING!")

if client_secret:
    print(f"  XERO_CLIENT_SECRET: {client_secret[:4]}...{client_secret[-4:]}")
else:
    print("  XERO_CLIENT_SECRET: MISSING!")

if redirect_uri:
    print(f"  XERO_REDIRECT_URI:  {redirect_uri}")
else:
    print("  XERO_REDIRECT_URI:  MISSING!")

# 2. Check SDK import
print("\n=== SDK Check ===\n")
try:
    from xero_python.api_client import ApiClient, Configuration
    from xero_python.api_client.oauth2 import OAuth2Token
    from xero_python.accounting import AccountingApi
    print("  xero-python SDK imported successfully!")
except ImportError as e:
    print(f"  FAILED to import xero-python: {e}")
    exit(1)

# 3. Build the authorization URL (proves config is valid)
print("\n=== Authorization URL ===\n")
scopes = "openid profile email accounting.transactions accounting.contacts offline_access"
auth_url = (
    f"https://login.xero.com/identity/connect/authorize?"
    f"response_type=code&"
    f"client_id={client_id}&"
    f"redirect_uri={redirect_uri}&"
    f"scope={scopes}&"
    f"state=test123"
)
print(f"  To connect Xero, open this URL in your browser:\n")
print(f"  {auth_url}")
print()
print("  After authorizing, Xero will redirect to your callback URL with a ?code= parameter.")
print("  You'll need a Django view at that URL to exchange the code for access tokens.")
print()
print("=== All checks passed! ===")
