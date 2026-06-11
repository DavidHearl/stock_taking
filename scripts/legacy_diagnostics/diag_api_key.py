#!/usr/bin/env python
import os
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

api_key = os.getenv('GOOGLE_MAPS_API_KEY', '')
print(f"Current API Key: {api_key[:20]}...")

if not api_key:
    print("❌ No API key found in .env file")
    exit(1)

# Test the API key with a simple geocoding request
test_address = "Belfast, Northern Ireland"
url = f"https://maps.googleapis.com/maps/api/geocode/json?address={test_address}&key={api_key}"

print(f"Testing API key with request to: {url}")
response = requests.get(url)

print(f"Response status: {response.status_code}")
if response.status_code == 200:
    data = response.json()
    if data.get('status') == 'OK':
        print("✅ API key appears to be valid!")
        print(f"Found {len(data.get('results', []))} results for '{test_address}'")
    elif data.get('status') == 'REQUEST_DENIED':
        print("❌ API key is invalid or not authorized")
        print(f"Error: {data.get('error_message', 'Unknown error')}")
    else:
        print(f"⚠️  Unexpected response status: {data.get('status')}")
        print(f"Response: {data}")
else:
    print(f"❌ HTTP error: {response.status_code}")
    print(f"Response: {response.text}")