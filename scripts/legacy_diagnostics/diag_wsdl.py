"""List all operations in the Anthill WSDL."""
import requests, re
from dotenv import load_dotenv
import os
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

resp = requests.get('https://sliderobes.anthillcrm.com/api/v1.asmx?WSDL', timeout=30)
print(f'HTTP {resp.status_code}, {len(resp.text)} bytes')

# Try several patterns since WSDL namespaces vary
for pattern in [r'operation name="([^"]+)"', r"operation name='([^']+)'", r'<wsdl:operation[^>]+name="([^"]+)"']:
    ops = sorted(set(re.findall(pattern, resp.text)))
    if ops:
        print(f'\n{len(ops)} operations (pattern: {pattern}):')
        for op in ops:
            print(f'  {op}')
        break

# Also search for "Appointment" anywhere
appt_lines = [l.strip() for l in resp.text.split('\n') if 'ppointment' in l or 'Fit' in l or 'Install' in l]
print(f'\nLines mentioning Appointment/Fit/Install: {len(appt_lines)}')
for l in appt_lines[:20]:
    print(f'  {l[:120]}')
