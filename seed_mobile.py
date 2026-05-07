"""
One-off script to seed initial mobile device data.
Run: python manage.py runscript seed_mobile_devices
Or:  python seed_mobile.py
"""
import os, sys, django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'stock_taking.settings')
django.setup()

from decimal import Decimal
from stock_take.models import MobileDevice

if MobileDevice.objects.exists():
    print("Mobile devices already exist – skipping seed.")
    sys.exit(0)

devices = [
    dict(is_esim=True,  device_type='',        model='',                    condition='',          chip='',               purchase_date='',              security_updates_until='',             serial_number='',           status='active',       phone_number='07535 320 422', first_name='David',    last_name='Hearl',   sim_cost=Decimal('16.50'), notes='',                                         is_dead=False),
    dict(is_esim=False, device_type='iphone',  model='SE (2nd Generation)', condition='excellent', chip='A13 Bionic',     purchase_date='November 2021', security_updates_until='2027',         serial_number='',           status='active',       phone_number='07956 201 958', first_name='Neil',     last_name='Robb',    sim_cost=Decimal('16.50'), notes='',                                         is_dead=False),
    dict(is_esim=False, device_type='iphone',  model='SE (2nd Generation)', condition='excellent', chip='A13 Bionic',     purchase_date='June 2021',     security_updates_until='2027',         serial_number='',           status='sim_removed',  phone_number='',              first_name='',         last_name='',        sim_cost=None,             notes='Sim Removed',                              is_dead=False),
    dict(is_esim=False, device_type='samsung', model='A13',                 condition='excellent', chip='Exynos 850',     purchase_date='',              security_updates_until='December 2025',serial_number='',           status='active',       phone_number='',              first_name='',         last_name='',        sim_cost=None,             notes='',                                         is_dead=False),
    dict(is_esim=False, device_type='samsung', model='A53 5G',              condition='excellent', chip='',               purchase_date='',              security_updates_until='March 2027',   serial_number='RZCT9249M1N',status='marketing',    phone_number='',              first_name='',         last_name='',        sim_cost=None,             notes='Marketing Phone - No Sim. Marketing Dead Phone', is_dead=False),
    dict(is_esim=False, device_type='google',  model='Pixel 3a',            condition='excellent', chip='Snapdragon 670', purchase_date='',              security_updates_until='May 2022',     serial_number='9BMAY1KFF3', status='active',       phone_number='07956 078 970', first_name='Andrew',   last_name='Breslin', sim_cost=Decimal('25.88'), notes='',                                         is_dead=False),
    dict(is_esim=False, device_type='samsung', model='A21s',                condition='excellent', chip='',               purchase_date='',              security_updates_until='May 2024',     serial_number='',           status='active',       phone_number='07956 078 972', first_name='Marketing',last_name='',        sim_cost=Decimal('16.50'), notes='REMOVE DEVICE',                            is_dead=False),
    dict(is_esim=False, device_type='samsung', model='A22 5G',              condition='excellent', chip='',               purchase_date='',              security_updates_until='August 2025',  serial_number='',           status='active',       phone_number='07760 883 476', first_name='Shauna',   last_name='Devine',  sim_cost=Decimal('16.50'), notes='',                                         is_dead=False),
    dict(is_esim=False, device_type='samsung', model='A23 5G',              condition='',          chip='',               purchase_date='',              security_updates_until='July 2025',    serial_number='',           status='active',       phone_number='07553 366 546', first_name='Pol',      last_name="O'Hagan", sim_cost=Decimal('23.88'), notes='',                                         is_dead=False),
    dict(is_esim=False, device_type='iphone',  model='SE (2nd Generation)', condition='excellent', chip='A13 Bionic',     purchase_date='',              security_updates_until='2027',         serial_number='',           status='active',       phone_number='07501 250 311', first_name='Adrian',   last_name='Leaf',    sim_cost=Decimal('23.88'), notes='',                                         is_dead=False),
    dict(is_esim=False, device_type='samsung', model='A21s',                condition='good',      chip='',               purchase_date='',              security_updates_until='May 2024',     serial_number='',           status='active',       phone_number='07377 625 031', first_name='Colin',    last_name='McGeown', sim_cost=Decimal('15.00'), notes='',                                         is_dead=False),
    dict(is_esim=False, device_type='iphone',  model='6',                   condition='poor',      chip='A8',             purchase_date='',              security_updates_until='',             serial_number='',           status='locked',       phone_number='',              first_name='',         last_name='',        sim_cost=None,             notes='',                                         is_dead=True),
    dict(is_esim=False, device_type='iphone',  model='6',                   condition='poor',      chip='A8',             purchase_date='',              security_updates_until='',             serial_number='',           status='locked',       phone_number='',              first_name='',         last_name='',        sim_cost=None,             notes='',                                         is_dead=True),
    dict(is_esim=False, device_type='iphone',  model='XR',                  condition='poor',      chip='',               purchase_date='October 2020',  security_updates_until='',             serial_number='',           status='cracked_screen',phone_number='',             first_name='',         last_name='',        sim_cost=None,             notes='Cracked Screen',                           is_dead=True),
    dict(is_esim=False, device_type='google',  model='Pixel 3a',            condition='ok',        chip='Snapdragon 670', purchase_date='',              security_updates_until='May 2022',     serial_number='',           status='cracked_screen',phone_number='',             first_name='',         last_name='',        sim_cost=None,             notes='Slightly Cracked Screen',                  is_dead=True),
    dict(is_esim=False, device_type='google',  model='Pixel 3a',            condition='excellent', chip='Snapdragon 670', purchase_date='',              security_updates_until='May 2022',     serial_number='9BMAY1KFF3', status='active',       phone_number='',              first_name='',         last_name='',        sim_cost=None,             notes='',                                         is_dead=True),
    dict(is_esim=False, device_type='samsung', model='A52s 5G',             condition='excellent', chip='Snapdragon 720G',purchase_date='',              security_updates_until='October 2025', serial_number='R5CR81B21SX',status='active',       phone_number='',              first_name='',         last_name='',        sim_cost=None,             notes='',                                         is_dead=True),
    dict(is_esim=False, device_type='samsung', model='A21s',                condition='excellent', chip='Exynos 850',     purchase_date='',              security_updates_until='',             serial_number='',           status='locked',       phone_number='',              first_name='',         last_name='',        sim_cost=None,             notes='',                                         is_dead=True),
]

created = 0
for d in devices:
    MobileDevice.objects.create(**d)
    created += 1

print(f"Seeded {created} mobile devices. Total: {MobileDevice.objects.count()}")
