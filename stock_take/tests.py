"""
Unit tests for the stock_take application.

Run with: python manage.py test stock_take
"""
import json
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.db.models import Sum
from django.test import TestCase, RequestFactory, Client, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    AnthillPayment, AnthillSale, Accessory, BoardsPO, Category, Customer,
    DesktopComponent, DesktopMachine, Expense, Fitter, Lead, OSDoor, Order, PNXItem, PagePermission,
    PurchaseOrder, Role, StockHistory, StockItem, Supplier, Timesheet, UserProfile,
)
from .forms import BoardsPOForm, OrderForm
from .dashboard_view import _contract_prefix_for_location, _get_monthly_sales_data, _contract_prefixes_for_locations
from .customer_views import _manual_amount_matches
from .permissions import get_user_permissions
from .services.location_filter import profile_locations, location_q


# ── Helpers ──────────────────────────────────────────────────────────────

def _create_user(username='testuser', password='testpass123', is_superuser=False):
    """Create a user (profile is auto-created by the post_save signal)."""
    user = User.objects.create_user(
        username=username, password=password,
        email=f'{username}@example.com',
    )
    if is_superuser:
        user.is_superuser = True
        user.save()
    return user


def _create_role(name='user'):
    return Role.objects.create(name=name)


def _create_customer(**kwargs):
    defaults = dict(name='Test Customer', email='test@example.com', is_active=True)
    defaults.update(kwargs)
    return Customer.objects.create(**defaults)


def _create_order(sale_number='123456', customer_number='012345', **kwargs):
    defaults = dict(sale_number=sale_number, customer_number=customer_number)
    defaults.update(kwargs)
    return Order.objects.create(**defaults)


def _create_stock_item(sku='SKU001', name='Widget', cost=10, quantity=50, **kwargs):
    defaults = dict(
        sku=sku, name=name, cost=Decimal(str(cost)),
        quantity=quantity, location='Warehouse', tracking_type='stock',
    )
    defaults.update(kwargs)
    return StockItem.objects.create(**defaults)


# ═══════════════════════════════════════════════════════════════════════
#  MODEL TESTS
# ═══════════════════════════════════════════════════════════════════════

class CustomerModelTests(TestCase):
    def test_str_with_name(self):
        c = _create_customer(name='Acme Corp')
        self.assertEqual(str(c), 'Acme Corp')

    def test_str_falls_back_to_first_last(self):
        c = _create_customer(name='', first_name='John', last_name='Doe')
        self.assertEqual(str(c), 'John Doe')

    def test_str_fallback_to_pk(self):
        c = _create_customer(name='', first_name='', last_name='')
        self.assertEqual(str(c), f'Customer #{c.pk}')

    def test_url_name_replaces_spaces(self):
        c = _create_customer(name='John Doe')
        self.assertEqual(c.url_name, 'John+Doe')


class LeadModelTests(TestCase):
    def test_lead_default_status(self):
        lead = Lead.objects.create(
            name='Jane Smith',
            email='jane@example.com', phone='123',
        )
        self.assertEqual(lead.status, 'new')


class OrderModelTests(TestCase):
    def test_time_allowance(self):
        order = _create_order(
            order_date=date(2025, 1, 1),
            fit_date=date(2025, 1, 15),
        )
        self.assertEqual(order.time_allowance(), 14)

    def test_time_allowance_none_when_dates_missing(self):
        order = _create_order()
        self.assertIsNone(order.time_allowance())

    def test_all_materials_ordered_when_manually_set(self):
        order = _create_order(all_items_ordered=True)
        self.assertTrue(order.all_materials_ordered)

    def test_all_materials_ordered_false_without_boards_po(self):
        order = _create_order()
        self.assertFalse(order.all_materials_ordered)

    def test_has_missing_accessories(self):
        order = _create_order()
        Accessory.objects.create(
            order=order, sku='SKU1', name='Hinge',
            cost_price=5, quantity=2, missing=True,
        )
        self.assertTrue(order.has_missing_accessories)

    def test_no_missing_accessories(self):
        order = _create_order()
        Accessory.objects.create(
            order=order, sku='SKU1', name='Hinge',
            cost_price=5, quantity=2, missing=False,
        )
        self.assertFalse(order.has_missing_accessories)

    def test_calculate_materials_cost_accessories(self):
        order = _create_order()
        Accessory.objects.create(
            order=order, sku='SKU1', name='Hinge',
            cost_price=Decimal('5.00'), quantity=3,
        )
        Accessory.objects.create(
            order=order, sku='SKU2', name='Bracket',
            cost_price=Decimal('2.50'), quantity=4,
        )
        # No boards or OS doors -> accessories only: 5*3 + 2.5*4 = 25
        cost = order.calculate_materials_cost()
        self.assertEqual(cost, Decimal('25.00'))

    def test_calculate_installation_cost(self):
        order = _create_order()
        fitter = Fitter.objects.create(name='Bob', hourly_rate=Decimal('20.00'))
        # Installation timesheet via PO
        po = PurchaseOrder.objects.create(
            workguru_id=999, number='PO999', status='Received',
            total=Decimal('500.00'), supplier_name='Fitter Co',
        )
        Timesheet.objects.create(
            order=order, timesheet_type='installation',
            fitter=fitter, date=date.today(), purchase_order=po,
        )
        # Expense
        Expense.objects.create(
            order=order, fitter=fitter, expense_type='petrol',
            amount=Decimal('45.00'), date=date.today(),
        )
        # 500 (PO) + 45 (expense) = 545
        self.assertEqual(order.calculate_installation_cost(), Decimal('545.00'))

    def test_calculate_manufacturing_cost(self):
        order = _create_order()
        Timesheet.objects.create(
            order=order, timesheet_type='manufacturing',
            date=date.today(), hours=Decimal('8'), hourly_rate=Decimal('15.00'),
        )
        # 8 * 15 = 120
        self.assertEqual(order.calculate_manufacturing_cost(), Decimal('120.00'))


class BoardsPOModelTests(TestCase):
    def test_boards_received_false_when_no_items(self):
        bpo = BoardsPO.objects.create(po_number='PO0001')
        self.assertFalse(bpo.boards_received)

    def test_boards_received_true(self):
        bpo = BoardsPO.objects.create(po_number='PO0002')
        PNXItem.objects.create(
            boards_po=bpo, barcode='BC1', matname='Oak',
            cleng=1000, cwidth=500, cnt=2, customer='123456',
            received_quantity=2,
        )
        self.assertTrue(bpo.boards_received)

    def test_boards_received_false_when_partial(self):
        bpo = BoardsPO.objects.create(po_number='PO0003')
        PNXItem.objects.create(
            boards_po=bpo, barcode='BC1', matname='Oak',
            cleng=1000, cwidth=500, cnt=5, customer='123456',
            received_quantity=3,
        )
        self.assertFalse(bpo.boards_received)


class PNXItemModelTests(TestCase):
    def setUp(self):
        self.bpo = BoardsPO.objects.create(po_number='PO-TEST')

    def test_is_fully_received(self):
        item = PNXItem.objects.create(
            boards_po=self.bpo, barcode='BC', matname='Wood',
            cleng=2000, cwidth=600, cnt=4, customer='123456',
            received_quantity=4,
        )
        self.assertTrue(item.is_fully_received)
        self.assertFalse(item.is_partially_received)

    def test_is_partially_received(self):
        item = PNXItem.objects.create(
            boards_po=self.bpo, barcode='BC', matname='Wood',
            cleng=2000, cwidth=600, cnt=4, customer='123456',
            received_quantity=2,
        )
        self.assertFalse(item.is_fully_received)
        self.assertTrue(item.is_partially_received)

    def test_get_cost(self):
        item = PNXItem.objects.create(
            boards_po=self.bpo, barcode='BC', matname='Sheet',
            cleng=Decimal('2000'), cwidth=Decimal('1000'),  # 2m x 1m = 2sqm
            cnt=Decimal('3'), customer='123456',
        )
        # 2sqm * 3 * 50/sqm = 300
        cost = item.get_cost(price_per_sqm=50)
        self.assertAlmostEqual(float(cost), 300.0, places=2)


class OSDoorModelTests(TestCase):
    def test_is_fully_received(self):
        order = _create_order()
        door = OSDoor.objects.create(
            customer=order, door_style='Flush', style_colour='White',
            item_description='Test', height=2000, width=800,
            colour='White', quantity=5, received_quantity=5,
        )
        self.assertTrue(door.is_fully_received)

    def test_is_partially_received(self):
        order = _create_order()
        door = OSDoor.objects.create(
            customer=order, door_style='Flush', style_colour='White',
            item_description='Test', height=2000, width=800,
            colour='White', quantity=5, received_quantity=2,
        )
        self.assertTrue(door.is_partially_received)
        self.assertFalse(door.is_fully_received)


class StockItemModelTests(TestCase):
    def test_total_value(self):
        item = _create_stock_item(cost=12.50, quantity=20)
        self.assertEqual(item.total_value, Decimal('250.00'))

    def test_str(self):
        item = _create_stock_item(sku='ABC', name='Bracket')
        self.assertEqual(str(item), 'ABC - Bracket')


class AccessoryProductsServiceTests(TestCase):
    """The accessory generator sources its products lookup live from
    StockItem (keyed on cad_sku) instead of a standalone products.db file."""

    def test_only_cad_bearing_items_included_and_join_shape(self):
        import os
        import sqlite3
        from stock_take.services.accessory_products import build_products_db_from_stock_items

        _create_stock_item(sku='WG_MATCH', name='Knob', cost=Decimal('2.350'), cad_sku='10.01.015')
        _create_stock_item(sku='WG_NOCAD', name='No CAD code', cost=Decimal('5.000'))

        path = build_products_db_from_stock_items()
        try:
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            cols = [r[1] for r in conn.execute('PRAGMA table_info(products)')]
            self.assertEqual(
                cols,
                ['wg_sku', 'cad_sku', 'name', 'description', 'cost_price', 'sell_price'],
            )
            rows = conn.execute('SELECT * FROM products').fetchall()
            # Only the item carrying a cad_sku is projected in.
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row['wg_sku'], 'WG_MATCH')
            self.assertEqual(row['cad_sku'], '10.01.015')
            self.assertAlmostEqual(row['cost_price'], 2.35)
            # sell_price is intentionally 0 so the downstream system keeps its own sell price.
            self.assertEqual(row['sell_price'], 0)
            conn.close()
        finally:
            os.unlink(path)
        self.assertFalse(os.path.exists(path))


class AnthillSaleModelTests(TestCase):
    def test_str(self):
        sale = AnthillSale.objects.create(
            anthill_activity_id='ACT001',
            customer_name='John Doe',
            sale_value=Decimal('5000'),
        )
        self.assertEqual(str(sale), 'Sale ACT001 - John Doe')

    def test_str_unknown_customer(self):
        sale = AnthillSale.objects.create(
            anthill_activity_id='ACT002',
        )
        self.assertEqual(str(sale), 'Sale ACT002 - Unknown')


class TimesheetModelTests(TestCase):
    def test_total_cost_manufacturing(self):
        ts = Timesheet(
            timesheet_type='manufacturing',
            date=date.today(),
            hours=Decimal('6'),
            hourly_rate=Decimal('18.50'),
        )
        self.assertEqual(ts.total_cost, Decimal('111.00'))

    def test_worker_name_fitter(self):
        fitter = Fitter.objects.create(name='Steve', hourly_rate=20)
        ts = Timesheet(
            timesheet_type='installation', fitter=fitter, date=date.today(),
        )
        self.assertEqual(ts.worker_name, 'Steve')

    def test_worker_name_unknown(self):
        ts = Timesheet(timesheet_type='installation', date=date.today())
        self.assertEqual(ts.worker_name, 'Unknown')


class AccessoryModelTests(TestCase):
    def test_is_cut_to_size(self):
        order = _create_order()
        acc = Accessory(order=order, sku='CTS1', name='Glass Cut To Size Panel')
        self.assertTrue(acc.is_cut_to_size)

    def test_not_cut_to_size(self):
        order = _create_order()
        acc = Accessory(order=order, sku='H1', name='Standard Hinge')
        self.assertFalse(acc.is_cut_to_size)

    def test_cut_size_display(self):
        order = _create_order()
        acc = Accessory(
            order=order, sku='CTS1', name='Panel',
            cut_width=Decimal('600'), cut_height=Decimal('2400'),
        )
        self.assertEqual(acc.cut_size_display, '600 x 2400mm')

    def test_cut_size_display_empty(self):
        order = _create_order()
        acc = Accessory(order=order, sku='H1', name='Hinge')
        self.assertEqual(acc.cut_size_display, '')


# ═══════════════════════════════════════════════════════════════════════
#  RBAC / PERMISSIONS TESTS
# ═══════════════════════════════════════════════════════════════════════

class RoleModelTests(TestCase):
    def test_admin_always_has_permission(self):
        role = _create_role('admin')
        self.assertTrue(role.has_page_permission('orders', 'view'))
        self.assertTrue(role.has_page_permission('anything', 'delete'))

    def test_user_role_no_permission_by_default(self):
        role = _create_role('user')
        self.assertFalse(role.has_page_permission('orders', 'view'))

    def test_user_role_with_explicit_permission(self):
        role = _create_role('user')
        PagePermission.objects.create(
            role=role, page_codename='orders',
            can_view=True, can_edit=True,
        )
        self.assertTrue(role.has_page_permission('orders', 'view'))
        self.assertTrue(role.has_page_permission('orders', 'edit'))
        self.assertFalse(role.has_page_permission('orders', 'delete'))

    def test_is_admin(self):
        self.assertTrue(_create_role('admin').is_admin())
        self.assertFalse(_create_role('user').is_admin())

    def test_get_accessible_pages(self):
        role = _create_role('user')
        PagePermission.objects.create(
            role=role, page_codename='orders', can_view=True,
        )
        PagePermission.objects.create(
            role=role, page_codename='dashboard', can_view=True,
        )
        pages = role.get_accessible_pages()
        self.assertIn('orders', pages)
        self.assertIn('dashboard', pages)
        self.assertNotIn('invoices', pages)


class UserProfilePermissionTests(TestCase):
    def test_superuser_has_all_permissions(self):
        user = _create_user('admin', is_superuser=True)
        self.assertTrue(user.profile.has_page_permission('anything'))

    def test_user_without_role_has_no_permissions(self):
        user = _create_user()
        user.profile.role = None
        user.profile.save()
        self.assertFalse(user.profile.has_page_permission('orders'))

    def test_convenience_methods(self):
        user = _create_user()
        role = _create_role('user')
        PagePermission.objects.create(
            role=role, page_codename='orders',
            can_view=True, can_create=True, can_edit=False, can_delete=False,
        )
        user.profile.role = role
        user.profile.save()
        self.assertTrue(user.profile.can_view('orders'))
        self.assertTrue(user.profile.can_create('orders'))
        self.assertFalse(user.profile.can_edit('orders'))
        self.assertFalse(user.profile.can_delete('orders'))

    def test_profile_auto_created_on_user_creation(self):
        """Ensure the post_save signal creates a UserProfile automatically."""
        user = User.objects.create_user('signaltest', password='pass123')
        self.assertTrue(hasattr(user, 'profile'))
        self.assertIsInstance(user.profile, UserProfile)


# ═══════════════════════════════════════════════════════════════════════
#  FORM VALIDATION TESTS
# ═══════════════════════════════════════════════════════════════════════

class BoardsPOFormTests(TestCase):
    def test_valid_po_number(self):
        form = BoardsPOForm(data={'po_number': 'PO1234', 'boards_ordered': False})
        self.assertTrue(form.is_valid())

    def test_invalid_po_number_no_prefix(self):
        form = BoardsPOForm(data={'po_number': '1234', 'boards_ordered': False})
        self.assertFalse(form.is_valid())
        self.assertIn('po_number', form.errors)


class OrderFormTests(TestCase):
    def test_valid_sale_number(self):
        form = OrderForm(data={
            'sale_number': '123456',
            'customer_number': '012345',
        })
        # Other required fields may fail, but sale_number should be clean
        if not form.is_valid():
            self.assertNotIn('sale_number', form.errors)

    def test_invalid_sale_number_too_short(self):
        form = OrderForm(data={
            'sale_number': '12345',
            'customer_number': '012345',
        })
        form.is_valid()
        self.assertIn('sale_number', form.errors)

    def test_invalid_sale_number_non_numeric(self):
        form = OrderForm(data={
            'sale_number': 'ABCDEF',
            'customer_number': '012345',
        })
        form.is_valid()
        self.assertIn('sale_number', form.errors)

    def test_valid_customer_number(self):
        form = OrderForm(data={
            'sale_number': '123456',
            'customer_number': '012345',
        })
        if not form.is_valid():
            self.assertNotIn('customer_number', form.errors)

    def test_invalid_customer_number_no_leading_zero(self):
        form = OrderForm(data={
            'sale_number': '123456',
            'customer_number': '123456',
        })
        form.is_valid()
        self.assertIn('customer_number', form.errors)

    def test_invalid_customer_number_too_short(self):
        form = OrderForm(data={
            'sale_number': '123456',
            'customer_number': '01234',
        })
        form.is_valid()
        self.assertIn('customer_number', form.errors)


# ═══════════════════════════════════════════════════════════════════════
#  DASHBOARD HELPER TESTS
# ═══════════════════════════════════════════════════════════════════════

class ContractPrefixTests(TestCase):
    def test_known_locations(self):
        self.assertEqual(_contract_prefix_for_location('belfast'), 'BFS')
        self.assertEqual(_contract_prefix_for_location('dublin'), 'DUB')
        self.assertEqual(_contract_prefix_for_location('nottingham'), 'NTG')
        self.assertEqual(_contract_prefix_for_location('wyedean'), 'WYE')
        self.assertEqual(_contract_prefix_for_location('midlands'), 'MDE')

    def test_case_insensitive(self):
        self.assertEqual(_contract_prefix_for_location('Belfast'), 'BFS')
        self.assertEqual(_contract_prefix_for_location('DUBLIN'), 'DUB')

    def test_whitespace_stripped(self):
        self.assertEqual(_contract_prefix_for_location('  belfast  '), 'BFS')

    def test_unknown_location_returns_empty(self):
        self.assertEqual(_contract_prefix_for_location('unknown'), '')
        self.assertEqual(_contract_prefix_for_location(''), '')


class MonthlySalesDataTests(TestCase):
    def test_no_sales_returns_zero(self):
        data = _get_monthly_sales_data(2025, 6)
        self.assertEqual(data['total'], 0)
        self.assertEqual(data['count'], 0)

    def test_sales_aggregated_correctly(self):
        AnthillSale.objects.create(
            anthill_activity_id='S1', sale_value=Decimal('1000'),
            fit_date=date(2025, 6, 10), status='open',
        )
        AnthillSale.objects.create(
            anthill_activity_id='S2', sale_value=Decimal('2000'),
            fit_date=date(2025, 6, 20), status='open',
        )
        data = _get_monthly_sales_data(2025, 6)
        self.assertEqual(data['total'], 3000.0)
        self.assertEqual(data['count'], 2)

    def test_cancelled_sales_excluded(self):
        AnthillSale.objects.create(
            anthill_activity_id='S3', sale_value=Decimal('5000'),
            fit_date=date(2025, 6, 15), status='cancelled',
        )
        data = _get_monthly_sales_data(2025, 6)
        self.assertEqual(data['count'], 0)

    def test_filtered_by_contract_prefix(self):
        AnthillSale.objects.create(
            anthill_activity_id='S4', sale_value=Decimal('1000'),
            fit_date=date(2025, 6, 10), contract_number='BFS-001',
        )
        AnthillSale.objects.create(
            anthill_activity_id='S5', sale_value=Decimal('2000'),
            fit_date=date(2025, 6, 10), contract_number='DUB-001',
        )
        data = _get_monthly_sales_data(2025, 6, prefixes=['BFS'])
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['total'], 1000.0)

    def test_filtered_by_multiple_prefixes(self):
        AnthillSale.objects.create(
            anthill_activity_id='S6', sale_value=Decimal('1000'),
            fit_date=date(2025, 6, 10), contract_number='BFS-001',
        )
        AnthillSale.objects.create(
            anthill_activity_id='S7', sale_value=Decimal('2000'),
            fit_date=date(2025, 6, 10), contract_number='DUB-001',
        )
        AnthillSale.objects.create(
            anthill_activity_id='S8', sale_value=Decimal('4000'),
            fit_date=date(2025, 6, 10), contract_number='NTG-001',
        )
        # Selecting Belfast + Dublin should combine both branches, excluding NTG.
        data = _get_monthly_sales_data(2025, 6, prefixes=['BFS', 'DUB'])
        self.assertEqual(data['count'], 2)
        self.assertEqual(data['total'], 3000.0)


class MultiLocationFilterTests(TestCase):
    """The site-wide location filter supports one or more branches."""

    def test_profile_parses_comma_list(self):
        user = _create_user()
        user.profile.selected_location = 'Belfast,Dublin'
        user.profile.save()
        self.assertEqual(user.profile.selected_location_list, ['Belfast', 'Dublin'])

    def test_profile_blank_is_all(self):
        user = _create_user()
        self.assertEqual(user.profile.selected_location_list, [])

    def test_profile_strips_and_drops_blanks(self):
        user = _create_user()
        user.profile.selected_location = ' Belfast , , Dublin '
        user.profile.save()
        self.assertEqual(user.profile.selected_location_list, ['Belfast', 'Dublin'])

    def test_location_q_none_when_empty(self):
        self.assertIsNone(location_q([], 'location'))

    def test_location_q_matches_any(self):
        _create_customer(name='A', location='Belfast')
        _create_customer(name='B', location='Dublin')
        _create_customer(name='C', location='Nottingham')
        q = location_q(['Belfast', 'Dublin'], 'location')
        names = set(Customer.objects.filter(q).values_list('name', flat=True))
        self.assertEqual(names, {'A', 'B'})

    def test_prefixes_for_multiple_locations(self):
        self.assertEqual(_contract_prefixes_for_locations(['Belfast', 'Dublin']), ['BFS', 'DUB'])

    def test_prefixes_splits_comma_entry(self):
        # A single comma-joined entry (e.g. a raw querystring param) is split.
        self.assertEqual(_contract_prefixes_for_locations(['Belfast,Dublin']), ['BFS', 'DUB'])

    def test_prefixes_dedup_and_skip_unknown(self):
        self.assertEqual(
            _contract_prefixes_for_locations(['Belfast', 'belfast', 'nowhere']), ['BFS']
        )

    def test_set_location_stores_multiple(self):
        # Only known locations (from Customer records) are accepted.
        _create_customer(name='A', location='Belfast')
        _create_customer(name='B', location='Dublin')
        from django.core.cache import cache
        cache.delete('nav_available_locations')
        user = _create_user()
        self.client.force_login(user)
        resp = self.client.post(
            reverse('set_location'),
            data=json.dumps({'locations': ['Belfast', 'Dublin', 'Bogus']}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(set(resp.json()['locations']), {'Belfast', 'Dublin'})
        user.profile.refresh_from_db()
        self.assertEqual(set(user.profile.selected_location_list), {'Belfast', 'Dublin'})


# ═══════════════════════════════════════════════════════════════════════
#  VIEW TESTS (authentication, status codes, redirects)
# ═══════════════════════════════════════════════════════════════════════

# Override both legacy and new-style storage settings for tests
_SIMPLE_STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}


@override_settings(
    STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
    STORAGES=_SIMPLE_STORAGES,
)
class DashboardViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = _create_user()
        # Give user a role with dashboard access
        role = _create_role('admin')
        self.user.profile.role = role
        self.user.profile.save()

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login', response.url)

    def test_dashboard_accessible_when_authenticated(self):
        self.client.login(username='testuser', password='testpass123')
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_franchise_user_redirected(self):
        franchise_role = Role.objects.filter(name='franchise').first()
        if not franchise_role:
            franchise_role = _create_role('franchise')
        self.user.profile.role = franchise_role
        self.user.profile.save()
        self.client.login(username='testuser', password='testpass123')
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 302)

    def test_monthly_sales_ajax(self):
        self.client.login(username='testuser', password='testpass123')
        response = self.client.get(
            reverse('dashboard_monthly_sales'),
            {'year': '2025', 'month': '6'},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])

    def test_branch_tabs_for_multiple_selected(self):
        self.user.profile.selected_location = 'Belfast,Dublin'
        self.user.profile.save()
        self.client.login(username='testuser', password='testpass123')
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.context['branch_tabs'], ['Belfast', 'Dublin'])
        self.assertEqual(response.context['active_branch'], '')

    def test_branch_tab_scopes_to_one_branch(self):
        self.user.profile.selected_location = 'Belfast,Dublin'
        self.user.profile.save()
        self.client.login(username='testuser', password='testpass123')
        response = self.client.get(reverse('dashboard'), {'branch': 'Belfast'})
        self.assertEqual(response.context['active_branch'], 'Belfast')

    def test_invalid_branch_falls_back_to_combined(self):
        self.user.profile.selected_location = 'Belfast,Dublin'
        self.user.profile.save()
        self.client.login(username='testuser', password='testpass123')
        response = self.client.get(reverse('dashboard'), {'branch': 'Bogus'})
        self.assertEqual(response.context['active_branch'], '')

    def test_sales_after_ajax(self):
        self.client.login(username='testuser', password='testpass123')
        response = self.client.get(
            reverse('dashboard_sales_after'),
            {'date': '2025-06-01'},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])


@override_settings(
    STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
    STORAGES=_SIMPLE_STORAGES,
)
class AuthViewTests(TestCase):
    """Test that key views require authentication."""

    def setUp(self):
        self.client = Client()

    def test_stock_list_unauthenticated_handled(self):
        """stock_list is accessible or redirects for unauthenticated users."""
        response = self.client.get(reverse('stock_list'))
        self.assertIn(response.status_code, [200, 302])

    def test_ordering_requires_login(self):
        response = self.client.get(reverse('ordering'))
        self.assertEqual(response.status_code, 302)


@override_settings(
    STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
    STORAGES=_SIMPLE_STORAGES,
)
class PermissionMiddlewareTests(TestCase):
    """Test that the RolePermissionMiddleware enforces access control."""

    def setUp(self):
        self.client = Client()
        self.user = _create_user('limited')
        role = _create_role('user')
        # Only grant dashboard access
        PagePermission.objects.create(
            role=role, page_codename='dashboard', can_view=True,
        )
        self.user.profile.role = role
        self.user.profile.save()

    def test_allowed_page(self):
        self.client.login(username='limited', password='testpass123')
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_forbidden_page(self):
        self.client.login(username='limited', password='testpass123')
        response = self.client.get(reverse('stock_list'))
        self.assertEqual(response.status_code, 403)


# ═══════════════════════════════════════════════════════════════════════
#  STOCK HISTORY TESTS
# ═══════════════════════════════════════════════════════════════════════

class StockHistoryTests(TestCase):
    def test_history_created(self):
        item = _create_stock_item()
        history = StockHistory.objects.create(
            stock_item=item, quantity=50, change_amount=10,
            change_type='purchase', reference='PO-100',
        )
        self.assertEqual(history.change_type, 'purchase')
        self.assertEqual(history.change_amount, 10)

    def test_history_ordering(self):
        """Most recent history entries should appear first."""
        item = _create_stock_item()
        h1 = StockHistory.objects.create(
            stock_item=item, quantity=50, change_amount=5, change_type='purchase',
        )
        h2 = StockHistory.objects.create(
            stock_item=item, quantity=55, change_amount=-3, change_type='sale',
        )
        entries = list(StockHistory.objects.filter(stock_item=item))
        self.assertEqual(entries[0].pk, h2.pk)
        self.assertEqual(entries[1].pk, h1.pk)


# ═══════════════════════════════════════════════════════════════════════
#  ANTHILL SALE + PAYMENT INTEGRATION TESTS
# ═══════════════════════════════════════════════════════════════════════

class AnthillPaymentTests(TestCase):
    def setUp(self):
        self.sale = AnthillSale.objects.create(
            anthill_activity_id='SALE-100',
            customer_name='Client A',
            sale_value=Decimal('10000'),
            contract_number='BFS-100',
            fit_date=date(2025, 7, 1),
        )

    def test_payments_linked_to_sale(self):
        AnthillPayment.objects.create(
            sale=self.sale, amount=Decimal('3000'),
            date=datetime(2025, 6, 1), source='xero',
        )
        AnthillPayment.objects.create(
            sale=self.sale, amount=Decimal('2000'),
            date=datetime(2025, 6, 15), source='xero',
        )
        total_paid = self.sale.payments.aggregate(
            total=Sum('amount')
        )['total']
        self.assertEqual(total_paid, Decimal('5000'))

    def test_outstanding_balance(self):
        AnthillPayment.objects.create(
            sale=self.sale, amount=Decimal('4000'),
            date=datetime(2025, 6, 1), source='xero',
        )
        total_paid = self.sale.payments.aggregate(
            total=Sum('amount')
        )['total'] or Decimal('0')
        outstanding = self.sale.sale_value - total_paid
        self.assertEqual(outstanding, Decimal('6000'))


# ═══════════════════════════════════════════════════════════════════════
#  ORDER WORKFLOW TESTS
# ═══════════════════════════════════════════════════════════════════════

class OrderBoardsReceivedTests(TestCase):
    def test_no_boards_po_returns_false(self):
        order = _create_order()
        self.assertFalse(order.order_boards_received)

    def test_boards_received_when_all_pnx_received(self):
        bpo = BoardsPO.objects.create(po_number='PO-RCV')
        order = _create_order(sale_number='654321', boards_po=bpo)
        PNXItem.objects.create(
            boards_po=bpo, barcode='BC1', matname='MDF',
            cleng=1500, cwidth=600, cnt=3, customer='654321',
            received_quantity=3,
        )
        self.assertTrue(order.order_boards_received)

    def test_boards_not_received_when_partial(self):
        bpo = BoardsPO.objects.create(po_number='PO-PARTIAL')
        order = _create_order(sale_number='654322', boards_po=bpo)
        PNXItem.objects.create(
            boards_po=bpo, barcode='BC1', matname='MDF',
            cleng=1500, cwidth=600, cnt=3, customer='654322',
            received_quantity=1,
        )
        self.assertFalse(order.order_boards_received)


class OrderOSDoorsTests(TestCase):
    def test_os_doors_received_when_all_received(self):
        order = _create_order(os_doors_required=True, os_doors_po='PO-DOOR')
        OSDoor.objects.create(
            customer=order, door_style='Hinged', style_colour='White',
            item_description='Bedroom Door', height=2000, width=800,
            colour='White', quantity=2, received_quantity=2,
        )
        self.assertTrue(order.os_doors_received)

    def test_os_doors_not_received_when_partial(self):
        order = _create_order(os_doors_required=True, os_doors_po='PO-DOOR2')
        OSDoor.objects.create(
            customer=order, door_style='Hinged', style_colour='Oak',
            item_description='Kitchen Door', height=2000, width=600,
            colour='Oak', quantity=4, received_quantity=2,
        )
        self.assertFalse(order.os_doors_received)

    def test_os_doors_not_required(self):
        order = _create_order(os_doors_required=False)
        self.assertFalse(order.os_doors_received)


# ═══════════════════════════════════════════════════════════════════════
#  IT DESKTOP MACHINE TESTS
# ═══════════════════════════════════════════════════════════════════════

@override_settings(
    STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
    STORAGES=_SIMPLE_STORAGES,
)
class DesktopMachineViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = _create_user('it_editor')
        role = _create_role('it_editor')
        PagePermission.objects.create(
            role=role,
            page_codename='desktop_devices',
            can_view=True,
            can_edit=True,
            can_delete=True,
        )
        self.user.profile.role = role
        self.user.profile.save()
        self.client.login(username='it_editor', password='testpass123')

    def _json_post(self, url, payload):
        return self.client.post(
            url,
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

    def test_create_desktop_machine_with_metrics(self):
        payload = {
            'name': 'Render Workstation',
            'vram_gb': 24,
            'pflops': 1.234,
            'components': [
                {'type': 'GPU', 'name': 'RTX 4090', 'source': 'Vendor A', 'price': '1599.99'},
                {'type': 'CPU', 'name': 'Ryzen 9', 'source': 'Vendor B', 'price': '599.50'},
            ],
        }

        response = self._json_post(reverse('desktop_machine_create'), payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])

        machine = DesktopMachine.objects.get(name='Render Workstation')
        self.assertEqual(machine.vram_gb, Decimal('24.00'))
        self.assertEqual(machine.pflops, Decimal('1.234'))

        components = list(machine.components.order_by('position'))
        self.assertEqual(len(components), 2)
        self.assertEqual(components[0].name, 'RTX 4090')
        self.assertEqual(components[1].name, 'Ryzen 9')
        self.assertEqual(components[0].position, 0)
        self.assertEqual(components[1].position, 1)

    def test_create_rejects_negative_metric(self):
        payload = {
            'name': 'Invalid Build',
            'vram_gb': -1,
            'components': [{'type': 'GPU', 'name': 'Test', 'price': '1.00'}],
        }

        response = self._json_post(reverse('desktop_machine_create'), payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn('cannot be negative', response.json()['error'])

    def test_create_requires_components(self):
        payload = {
            'name': 'No Components',
            'vram_gb': 8,
            'pflops': 0.5,
            'components': [],
        }

        response = self._json_post(reverse('desktop_machine_create'), payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn('At least one component is required', response.json()['error'])

    def test_update_replaces_components_and_metrics(self):
        machine = DesktopMachine.objects.create(name='Original', vram_gb=Decimal('8.00'), pflops=Decimal('0.200'))
        DesktopComponent.objects.create(
            machine=machine,
            component_type='GPU',
            name='Old GPU',
            source='Old Source',
            price=Decimal('123.45'),
            position=0,
        )

        payload = {
            'name': 'Updated Build',
            'vram_gb': 48,
            'pflops': 2.5,
            'components': [
                {'type': 'GPU', 'name': 'New GPU', 'source': 'Vendor A', 'price': '1999.00'},
                {'type': 'RAM', 'name': '128GB DDR5', 'source': 'Vendor B', 'price': '399.00'},
            ],
        }

        response = self._json_post(reverse('desktop_machine_save', args=[machine.id]), payload)
        self.assertEqual(response.status_code, 200)

        machine.refresh_from_db()
        self.assertEqual(machine.name, 'Updated Build')
        self.assertEqual(machine.vram_gb, Decimal('48.00'))
        self.assertEqual(machine.pflops, Decimal('2.500'))

        components = list(machine.components.order_by('position'))
        self.assertEqual(len(components), 2)
        self.assertEqual([c.name for c in components], ['New GPU', '128GB DDR5'])

    def test_save_requires_edit_permission(self):
        restricted_user = _create_user('it_viewer')
        restricted_role = _create_role('it_viewer')
        PagePermission.objects.create(
            role=restricted_role,
            page_codename='desktop_devices',
            can_view=True,
            can_edit=False,
        )
        restricted_user.profile.role = restricted_role
        restricted_user.profile.save()

        self.client.logout()
        self.client.login(username='it_viewer', password='testpass123')

        payload = {
            'name': 'Blocked Build',
            'components': [{'type': 'GPU', 'name': 'Test', 'price': '1.00'}],
        }
        response = self._json_post(reverse('desktop_machine_create'), payload)
        self.assertEqual(response.status_code, 403)


class EnquiryCustomerMatchingTests(TestCase):
    """Matching website enquiries against existing Atlas customers."""

    def _enquiry(self, **kwargs):
        from .models import WebsiteEnquiry
        defaults = dict(name='Jane Smith')
        defaults.update(kwargs)
        return WebsiteEnquiry.objects.create(**defaults)

    def _match(self, enquiry):
        from .services.enquiry_matching import find_customer_matches
        return find_customer_matches([enquiry]).get(enquiry.pk)

    def test_matches_on_email_case_insensitive(self):
        cust = _create_customer(name='Jane Smith', email='jane@example.com')
        enq = self._enquiry(email='JANE@example.com')
        match = self._match(enq)
        self.assertIsNotNone(match)
        self.assertEqual(match['tier'], 'email')
        self.assertEqual(match['confidence'], 'high')
        self.assertEqual(match['customer_id'], cust.pk)

    def test_matches_on_normalised_phone(self):
        cust = _create_customer(name='Jane Smith', email='', phone='07700 900000')
        enq = self._enquiry(email='nomatch@example.com', phone='+44 7700 900000')
        match = self._match(enq)
        self.assertIsNotNone(match)
        self.assertEqual(match['tier'], 'phone')
        self.assertEqual(match['customer_id'], cust.pk)

    def test_matches_on_last_name_and_postcode_from_address(self):
        cust = _create_customer(name='Jane Smith', email='', last_name='Smith', postcode='BT1 1AA')
        enq = self._enquiry(name='Jane Smith', last_name='Smith', address='12 Some Road, Belfast BT1 1AA')
        match = self._match(enq)
        self.assertIsNotNone(match)
        self.assertEqual(match['tier'], 'name_postcode')
        self.assertEqual(match['customer_id'], cust.pk)

    def test_no_match_returns_none(self):
        _create_customer(name='Jane Smith', email='jane@example.com')
        enq = self._enquiry(name='Nobody Here', email='stranger@example.com', phone='0000')
        self.assertIsNone(self._match(enq))

    def test_email_takes_priority_over_phone(self):
        by_email = _create_customer(name='Email Match', email='jane@example.com', phone='999')
        _create_customer(name='Phone Match', email='other@example.com', phone='07700 900000')
        enq = self._enquiry(email='jane@example.com', phone='07700 900000')
        match = self._match(enq)
        self.assertEqual(match['tier'], 'email')
        self.assertEqual(match['customer_id'], by_email.pk)

    def test_order_count_included(self):
        cust = _create_customer(name='Jane Smith', email='jane@example.com')
        _create_order(sale_number='100001', customer=cust)
        _create_order(sale_number='100002', customer=cust)
        match = self._match(self._enquiry(email='jane@example.com'))
        self.assertEqual(match['order_count'], 2)

    def test_ambiguous_flag_when_multiple_customers_share_email(self):
        _create_customer(name='First Dup', email='dup@example.com')
        _create_customer(name='Second Dup', email='dup@example.com')
        match = self._match(self._enquiry(email='dup@example.com'))
        self.assertTrue(match['ambiguous'])

    def test_inactive_customers_are_ignored(self):
        _create_customer(name='Gone', email='jane@example.com', is_active=False)
        self.assertIsNone(self._match(self._enquiry(email='jane@example.com')))


class ReconcileGallerySalePhotosTests(TestCase):
    """Phase A gallery-image linking in reconcile_gallery_sale_photos.

    Photos live in GalleryImage; a sale shows only those with order == sale.order.
    The command fills blank order/customer where the match is unambiguous and
    leaves genuinely ambiguous rows alone.
    """

    def _image(self, **kwargs):
        from .models import GalleryImage
        # ImageField only stores a name here — Phase A never opens the file.
        defaults = dict(image='gallery/x.jpg')
        defaults.update(kwargs)
        return GalleryImage.objects.create(**defaults)

    def _run(self, **opts):
        from io import StringIO
        from django.core.management import call_command
        call_command('reconcile_gallery_sale_photos', fix=True, stdout=StringIO(), **opts)

    def test_mirrors_customer_from_order(self):
        cust = _create_customer(name='Alice')
        order = _create_order(sale_number='500001', customer=cust)
        img = self._image(order=order)
        self._run()
        img.refresh_from_db()
        self.assertEqual(img.customer_id, cust.id)

    def test_links_order_when_customer_has_single_order(self):
        cust = _create_customer(name='Bob')
        order = _create_order(sale_number='500002', customer=cust)
        img = self._image(customer=cust)
        self._run()
        img.refresh_from_db()
        self.assertEqual(img.order_id, order.id)

    def test_links_order_via_sale_number_in_caption(self):
        cust = _create_customer(name='Carol')
        target = _create_order(sale_number='500003', customer=cust)
        _create_order(sale_number='500004', customer_number='999999', customer=cust)
        img = self._image(customer=cust, caption='Fit photos for 500003')
        self._run()
        img.refresh_from_db()
        self.assertEqual(img.order_id, target.id)

    def test_ambiguous_multiple_orders_left_unlinked(self):
        cust = _create_customer(name='Dave')
        _create_order(sale_number='500005', customer=cust)
        _create_order(sale_number='500006', customer_number='999998', customer=cust)
        img = self._image(customer=cust)
        self._run()
        img.refresh_from_db()
        self.assertIsNone(img.order_id)

    def test_existing_order_is_never_overwritten(self):
        cust = _create_customer(name='Erin')
        keep = _create_order(sale_number='500007', customer=cust)
        _create_order(sale_number='500008', customer_number='999997', customer=cust)
        img = self._image(order=keep, customer=cust)
        self._run()
        img.refresh_from_db()
        self.assertEqual(img.order_id, keep.id)

    def test_dry_run_writes_nothing(self):
        cust = _create_customer(name='Frank')
        _create_order(sale_number='500009', customer=cust)
        img = self._image(customer=cust)
        from io import StringIO
        from django.core.management import call_command
        call_command('reconcile_gallery_sale_photos', stdout=StringIO())  # dry-run default
        img.refresh_from_db()
        self.assertIsNone(img.order_id)


class DistributePaymentsViewTests(TestCase):
	"""Distributing must retire the payments it re-spreads.

	Writing the spread while leaving the source payments active counts the same
	money twice and shows a phantom account credit on every sale for that
	customer (see _build_customer_payment_pool).
	"""

	def setUp(self):
		self.client = Client()
		self.user = _create_user()
		self.user.profile.role = _create_role('admin')
		self.user.profile.save()
		self.client.login(username='testuser', password='testpass123')

		self.customer = _create_customer(name='Pool Customer')
		self.sale_a = AnthillSale.objects.create(
			anthill_activity_id='ACT100', customer=self.customer,
			customer_name='Pool Customer', sale_value=Decimal('6000'),
		)
		self.sale_b = AnthillSale.objects.create(
			anthill_activity_id='ACT101', customer=self.customer,
			customer_name='Pool Customer', sale_value=Decimal('4000'),
		)
		# A single £9,000 invoice paid against sale A that really covers both jobs.
		self.source = AnthillPayment.objects.create(
			sale=self.sale_a, source='xero', payment_type='Payment',
			xero_invoice_id='INV-UUID-1', xero_invoice_number='INV-0001',
			amount=Decimal('9000'), status='Confirmed',
		)
		self.url = reverse('customer_distribute_payments', args=[self.customer.pk])

	def _post(self, payload):
		return self.client.post(
			self.url, data=json.dumps(payload), content_type='application/json',
		)

	def _active_total(self):
		return sum(
			p.amount for p in AnthillPayment.objects.filter(
				sale__customer=self.customer, ignored=False,
			)
		)

	def test_distribute_retires_source_payments(self):
		response = self._post({
			'invoice_ids': ['INV-UUID-1'],
			'distributions': [
				{'sale_pk': self.sale_a.pk, 'amount': '5000.00'},
				{'sale_pk': self.sale_b.pk, 'amount': '4000.00'},
			],
		})
		self.assertEqual(response.status_code, 200)
		body = response.json()
		self.assertTrue(body['success'])
		self.assertEqual(body['sources_ignored'], 1)

		self.source.refresh_from_db()
		self.assertTrue(self.source.ignored)
		# £9,000 spread, not £18,000 counted twice.
		self.assertEqual(self._active_total(), Decimal('9000.00'))

	def test_redistributing_replaces_rather_than_stacks(self):
		payload = {
			'invoice_ids': ['INV-UUID-1'],
			'distributions': [
				{'sale_pk': self.sale_a.pk, 'amount': '5000.00'},
				{'sale_pk': self.sale_b.pk, 'amount': '4000.00'},
			],
		}
		self._post(payload)
		payload['distributions'] = [
			{'sale_pk': self.sale_a.pk, 'amount': '6000.00'},
			{'sale_pk': self.sale_b.pk, 'amount': '3000.00'},
		]
		response = self._post(payload)
		self.assertTrue(response.json()['success'])

		dist_rows = AnthillPayment.objects.filter(
			sale__customer=self.customer, payment_type='Xero Distribution', ignored=False,
		)
		self.assertEqual(dist_rows.count(), 2)
		self.assertEqual(self._active_total(), Decimal('9000.00'))

	def test_over_allocation_is_rejected(self):
		response = self._post({
			'invoice_ids': ['INV-UUID-1'],
			'distributions': [
				{'sale_pk': self.sale_a.pk, 'amount': '6000.00'},
				{'sale_pk': self.sale_b.pk, 'amount': '4000.00'},
			],
		})
		self.assertEqual(response.status_code, 400)
		self.source.refresh_from_db()
		self.assertFalse(self.source.ignored)
		self.assertFalse(
			AnthillPayment.objects.filter(payment_type='Xero Distribution').exists()
		)

	def test_missing_invoice_ids_is_rejected(self):
		response = self._post({
			'invoice_ids': [],
			'distributions': [{'sale_pk': self.sale_a.pk, 'amount': '9000.00'}],
		})
		self.assertEqual(response.status_code, 400)
		self.source.refresh_from_db()
		self.assertFalse(self.source.ignored)

	def test_unknown_sale_writes_nothing(self):
		other = _create_customer(name='Someone Else')
		foreign = AnthillSale.objects.create(
			anthill_activity_id='ACT999', customer=other, sale_value=Decimal('100'),
		)
		response = self._post({
			'invoice_ids': ['INV-UUID-1'],
			'distributions': [
				{'sale_pk': self.sale_a.pk, 'amount': '1000.00'},
				{'sale_pk': foreign.pk, 'amount': '500.00'},
			],
		})
		self.assertEqual(response.status_code, 400)
		self.source.refresh_from_db()
		self.assertFalse(self.source.ignored)
		self.assertFalse(
			AnthillPayment.objects.filter(payment_type='Xero Distribution').exists()
		)


class XeroMatchManualPaymentTests(TestCase):
	"""Xero is the source of truth: matching imports the invoice and removes the
	manual placeholder, so the same money is never counted twice."""

	def setUp(self):
		self.client = Client()
		self.user = _create_user()
		self.user.profile.role = _create_role('admin')
		self.user.profile.save()
		self.client.login(username='testuser', password='testpass123')

		self.customer = _create_customer(name='Joanna Test')
		self.sale = AnthillSale.objects.create(
			anthill_activity_id='ACT200', customer=self.customer,
			customer_name='Joanna Test', contract_number='BFS-NR-ACT200',
			sale_value=Decimal('2000'),
		)
		self.manual = AnthillPayment.objects.create(
			sale=self.sale, source='manual', payment_type='Stock',
			amount=Decimal('593.25'), status='Confirmed',
		)
		self.url = reverse('xero_match_manual_payment', args=[self.sale.pk])
		self.invoice = {
			'InvoiceID': 'INV-UUID-9', 'InvoiceNumber': 'INV-1204',
			'Total': '593.25', 'AmountDue': '0', 'AmountPaid': '593.25',
			'Status': 'PAID',
			'Payments': [{
				'PaymentID': 'PAY-UUID-9', 'Amount': '593.25',
				'Date': '2026-06-05', 'Reference': 'Payment', 'Status': 'AUTHORISED',
			}],
		}

	def _post(self, **payload):
		return self.client.post(
			self.url, data=json.dumps(payload), content_type='application/json',
		)

	def _patch(self, invoice):
		from unittest.mock import patch
		return patch(
			'stock_take.services.xero_api.get_invoice_with_payments',
			return_value=invoice,
		)

	def test_match_imports_invoice_and_deletes_manual(self):
		with self._patch(self.invoice):
			response = self._post(invoice_id='INV-UUID-9', payment_pk=self.manual.pk)

		self.assertEqual(response.status_code, 200)
		self.assertTrue(response.json()['success'])
		self.assertFalse(AnthillPayment.objects.filter(pk=self.manual.pk).exists())

		imported = AnthillPayment.objects.get(sale=self.sale, anthill_payment_id='PAY-UUID-9')
		self.assertEqual(imported.source, 'xero')
		self.assertEqual(imported.amount, Decimal('593.25'))
		# £593.25 recorded once, not twice.
		self.assertEqual(
			sum(p.amount for p in self.sale.payments.filter(ignored=False)),
			Decimal('593.25'),
		)

	def test_amount_mismatch_is_rejected_and_manual_survives(self):
		invoice = dict(self.invoice, Total='820.00', AmountPaid='820.00')
		with self._patch(invoice):
			response = self._post(invoice_id='INV-UUID-9', payment_pk=self.manual.pk)

		self.assertEqual(response.status_code, 400)
		self.assertTrue(AnthillPayment.objects.filter(pk=self.manual.pk).exists())
		self.assertFalse(AnthillPayment.objects.filter(source='xero').exists())

	def test_non_manual_payment_cannot_be_matched(self):
		xero_payment = AnthillPayment.objects.create(
			sale=self.sale, source='xero', payment_type='Payment',
			amount=Decimal('593.25'), status='Confirmed',
		)
		with self._patch(self.invoice):
			response = self._post(invoice_id='INV-UUID-9', payment_pk=xero_payment.pk)

		self.assertEqual(response.status_code, 400)
		self.assertTrue(AnthillPayment.objects.filter(pk=xero_payment.pk).exists())

	def test_payment_from_another_sale_is_rejected(self):
		other = AnthillSale.objects.create(
			anthill_activity_id='ACT201', customer=self.customer, sale_value=Decimal('500'),
		)
		foreign = AnthillPayment.objects.create(
			sale=other, source='manual', payment_type='Stock',
			amount=Decimal('593.25'), status='Confirmed',
		)
		with self._patch(self.invoice):
			response = self._post(invoice_id='INV-UUID-9', payment_pk=foreign.pk)

		self.assertEqual(response.status_code, 404)
		self.assertTrue(AnthillPayment.objects.filter(pk=foreign.pk).exists())

	def test_missing_arguments_are_rejected(self):
		response = self._post(invoice_id='INV-UUID-9')
		self.assertEqual(response.status_code, 400)


class ManualAmountMatchTests(TestCase):
	def test_matches_invoice_total(self):
		self.assertTrue(_manual_amount_matches(Decimal('593.25'), '593.25', '0'))

	def test_matches_amount_paid(self):
		self.assertTrue(_manual_amount_matches(Decimal('593.25'), '1200.00', '593.25'))

	def test_penny_tolerance(self):
		self.assertTrue(_manual_amount_matches(Decimal('593.25'), '593.26', '0'))
		self.assertFalse(_manual_amount_matches(Decimal('593.25'), '593.30', '0'))

	def test_zero_invoice_never_matches(self):
		self.assertFalse(_manual_amount_matches(Decimal('0'), '0', '0'))

	def test_none_amount_never_matches(self):
		self.assertFalse(_manual_amount_matches(None, '593.25', '593.25'))


# ═══════════════════════════════════════════════════════════════════════
#  ACCOUNTS PAYABLE
# ═══════════════════════════════════════════════════════════════════════

class AccountsPayableAttachmentFilterTests(TestCase):
	"""The inbox only deals with email that can actually become an invoice."""

	def setUp(self):
		from .models import MailboxEmail
		self.MailboxEmail = MailboxEmail
		base = dict(sender_email='ap@supplier.com', received_at=timezone.now())
		self.with_att = MailboxEmail.objects.create(
			graph_message_id='m-with', subject='Invoice 1',
			attachment_names='[{"id": "a1", "name": "inv.pdf"}]', **base
		)
		self.blank = MailboxEmail.objects.create(
			graph_message_id='m-blank', subject='No attachment', attachment_names='', **base
		)
		self.empty_list = MailboxEmail.objects.create(
			graph_message_id='m-empty', subject='Empty list', attachment_names='[]', **base
		)

	def test_keeps_only_emails_with_attachments(self):
		from .match_invoices_views import _with_attachments
		ids = set(_with_attachments(self.MailboxEmail.objects.all()).values_list('id', flat=True))
		self.assertEqual(ids, {self.with_att.id})

	def test_excludes_blank_and_empty_json(self):
		from .match_invoices_views import _with_attachments
		kept = _with_attachments(self.MailboxEmail.objects.all())
		self.assertNotIn(self.blank, kept)
		self.assertNotIn(self.empty_list, kept)


class MatchInvoicesPageTests(TestCase):
	"""The Match Invoices page is driven by POs awaiting a supplier invoice."""

	def setUp(self):
		self.client = Client()
		self.user = _create_user(is_superuser=True)
		self.client.login(username='testuser', password='testpass123')
		# Received, no linked invoice, dated in-window -> awaiting an invoice.
		self.po = PurchaseOrder.objects.create(
			workguru_id=9001, display_number='PO9001', status='Received',
			supplier_id=555, supplier_name='Acme Timber',
			received_date='2026-03-04', total=Decimal('1250.00'),
		)
		# Excluded: explicitly flagged as needing no invoice.
		PurchaseOrder.objects.create(
			workguru_id=9002, display_number='PO9002', status='Received',
			supplier_id=556, supplier_name='No Invoice Ltd',
			received_date='2026-03-05', total=Decimal('99.00'),
			invoice_not_required=True,
		)
		# Excluded: not received yet.
		PurchaseOrder.objects.create(
			workguru_id=9003, display_number='PO9003', status='Draft',
			supplier_id=557, supplier_name='Draft Supplies',
			received_date='2026-03-06', total=Decimal('10.00'),
		)

	def test_page_loads(self):
		response = self.client.get(reverse('match_invoices'))
		self.assertEqual(response.status_code, 200)

	def test_lists_only_pos_awaiting_an_invoice(self):
		response = self.client.get(reverse('match_invoices'))
		listed = {po.workguru_id for po in response.context['pos']}
		self.assertIn(9001, listed)
		self.assertNotIn(9002, listed)
		self.assertNotIn(9003, listed)

	def test_supplier_options_come_from_awaiting_pos(self):
		response = self.client.get(reverse('match_invoices'))
		suppliers = response.context['po_suppliers']
		names = [s['name'] for s in suppliers]
		self.assertEqual(names, ['Acme Timber'])
		self.assertEqual(suppliers[0]['supplier_id'], 555)
		self.assertEqual(suppliers[0]['po_count'], 1)

	def test_suppliers_context_is_plain_names_for_rules_modal(self):
		"""The shared rules modal renders `suppliers` as <option> strings."""
		Supplier.objects.create(workguru_id=555, name='Acme Timber')
		response = self.client.get(reverse('match_invoices'))
		self.assertEqual(list(response.context['suppliers']), ['Acme Timber'])

	def test_awaiting_total_sums_po_values(self):
		response = self.client.get(reverse('match_invoices'))
		self.assertEqual(response.context['awaiting_total'], Decimal('1250.00'))
		self.assertEqual(response.context['awaiting_count'], 1)


class MatchInvoicesInboxTests(TestCase):
	"""The mailbox is a table-only fragment rendered inside the Match Invoices modal."""

	def setUp(self):
		self.client = Client()
		self.user = _create_user(is_superuser=True)
		self.client.login(username='testuser', password='testpass123')

	def test_fragment_returns_table_without_page_chrome(self):
		"""The modal renders this inline, so it must not carry the layout."""
		response = self.client.get(reverse('match_invoices_inbox_fragment'))
		self.assertEqual(response.status_code, 200)
		body = response.content.decode()
		self.assertIn('apay-table-container', body)
		self.assertNotIn('<body', body)
		self.assertNotIn('apay-toolbar', body)
		self.assertNotIn('<script', body)

	def test_fragment_honours_status_filter(self):
		from .models import MailboxEmail
		MailboxEmail.objects.create(
			graph_message_id='m-ig', subject='Ignored one', sender_email='a@b.com',
			received_at=timezone.now(), attachment_names='[{"id": "a1", "name": "i.pdf"}]',
			is_ignored=True,
		)
		visible = self.client.get(reverse('match_invoices_inbox_fragment'), {'status': 'ignored'})
		self.assertIn('Ignored one', visible.content.decode())
		hidden = self.client.get(reverse('match_invoices_inbox_fragment'))
		self.assertNotIn('Ignored one', hidden.content.decode())


class BookOrderFitDateTests(TestCase):
	"""Booking a fit date from the sale page's install-date workflow step."""

	def setUp(self):
		from .models import Fitter
		self.client = Client()
		self.user = _create_user(is_superuser=True)
		self.client.login(username='testuser', password='testpass123')
		self.order = _create_order()
		Fitter.objects.create(code='R', name='Ross Middleton', active=True)

	def _post(self, **payload):
		return self.client.post(
			reverse('book_order_fit_date', args=[self.order.id]),
			data=json.dumps(payload), content_type='application/json',
		)

	def test_creates_confirmed_appointment_and_syncs_order(self):
		from .models import FitAppointment
		response = self._post(fit_date='2026-09-01', fitter='R')
		self.assertEqual(response.status_code, 200)
		appointment = FitAppointment.objects.get(order=self.order)
		self.assertEqual(appointment.fit_date, date(2026, 9, 1))
		# Booking here is deliberate, unlike a calendar drag.
		self.assertFalse(appointment.is_provisional)
		self.order.refresh_from_db()
		self.assertEqual(self.order.fit_date, date(2026, 9, 1))

	def test_confirms_and_moves_an_existing_provisional_appointment(self):
		from .models import FitAppointment
		appointment = FitAppointment.objects.create(
			order=self.order, fit_date=date(2026, 8, 1), fitter='R', is_provisional=True,
		)
		self._post(fit_date='2026-09-15', fitter='R')
		appointment.refresh_from_db()
		self.assertEqual(appointment.fit_date, date(2026, 9, 15))
		self.assertFalse(appointment.is_provisional)
		self.assertEqual(FitAppointment.objects.filter(order=self.order).count(), 1)

	def test_allows_a_legacy_fitter_code_already_on_the_appointment(self):
		"""Codes predating the current Fitter roster must not block a re-book."""
		from .models import FitAppointment
		FitAppointment.objects.create(
			order=self.order, fit_date=date(2026, 8, 1), fitter='K', is_provisional=True,
		)
		response = self._post(fit_date='2026-09-15', fitter='K')
		self.assertEqual(response.status_code, 200)

	def test_rejects_missing_date_unknown_fitter_and_get(self):
		from .models import FitAppointment
		self.assertEqual(self._post(fitter='R').status_code, 400)
		self.assertEqual(self._post(fit_date='not-a-date', fitter='R').status_code, 400)
		self.assertEqual(self._post(fit_date='2026-09-01', fitter='Z').status_code, 400)
		self.assertEqual(
			self.client.get(reverse('book_order_fit_date', args=[self.order.id])).status_code, 405
		)
		self.assertFalse(FitAppointment.objects.filter(order=self.order).exists())


class StockPaymentOverrideTests(TestCase):
	"""The stock payment tick is a manual override, not a hard gate."""

	def setUp(self):
		self.client = Client()
		self.user = _create_user(is_superuser=True)
		self.client.login(username='testuser', password='testpass123')
		self.order = _create_order()

	def _post(self, confirmed):
		return self.client.post(
			reverse('set_order_stock_payment', args=[self.order.id]),
			data=json.dumps({'confirmed': confirmed}), content_type='application/json',
		)

	def test_ticking_and_unticking_persists(self):
		self.assertEqual(self._post(True).status_code, 200)
		self.order.refresh_from_db()
		self.assertTrue(self.order.stock_payment_confirmed)
		self._post(False)
		self.order.refresh_from_db()
		self.assertFalse(self.order.stock_payment_confirmed)

	def test_get_is_rejected(self):
		self.assertEqual(
			self.client.get(reverse('set_order_stock_payment', args=[self.order.id])).status_code, 405
		)

	def test_manual_tick_satisfies_the_step_without_a_recorded_payment(self):
		"""No stock payment row exists, so the override is the only thing ticking it."""
		from .models import AnthillPayment, AnthillSale, OrderWorkflowProgress, WorkflowStage
		from .views import _build_order_context
		stage = WorkflowStage.objects.create(
			name='Arrange Install Date & Take Stock Payment', phase='sale',
			role='customer-support', description='', order=1,
		)
		OrderWorkflowProgress.objects.create(order=self.order, current_stage=stage)
		AnthillSale.objects.create(anthill_activity_id='426201', order=self.order)

		request = RequestFactory().get('/')
		request.user = self.user
		request.session = self.client.session

		context = _build_order_context(self.order, request)
		self.assertFalse(context['install_payment_taken'])

		self._post(True)
		context = _build_order_context(Order.objects.get(id=self.order.id), request)
		self.assertTrue(context['install_payment_taken'])
		# Still flagged as not actually recorded, so the tick stays editable.
		self.assertFalse(context['install_payment_recorded'])

	def test_a_recorded_stock_payment_ticks_it_without_the_override(self):
		from .models import AnthillPayment, AnthillSale, OrderWorkflowProgress, WorkflowStage
		from .views import _build_order_context
		stage = WorkflowStage.objects.create(
			name='Arrange Install Date & Take Stock Payment', phase='sale',
			role='customer-support', description='', order=1,
		)
		OrderWorkflowProgress.objects.create(order=self.order, current_stage=stage)
		sale = AnthillSale.objects.create(anthill_activity_id='426202', order=self.order)
		AnthillPayment.objects.create(sale=sale, payment_type='Stock Payment', amount=Decimal('500'))

		request = RequestFactory().get('/')
		request.user = self.user
		request.session = self.client.session
		context = _build_order_context(self.order, request)
		self.assertTrue(context['install_payment_taken'])
		self.assertTrue(context['install_payment_recorded'])


class FitDateConfirmedToggleTests(TestCase):
	"""The install-date tick is the calendar's provisional flag, both ways."""

	def setUp(self):
		from .models import FitAppointment
		self.client = Client()
		self.user = _create_user(is_superuser=True)
		self.client.login(username='testuser', password='testpass123')
		self.order = _create_order()
		self.appointment = FitAppointment.objects.create(
			order=self.order, fit_date=date(2026, 9, 1), fitter='R', is_provisional=True,
		)

	def _post(self, confirmed):
		return self.client.post(
			reverse('set_order_fit_confirmed', args=[self.order.id]),
			data=json.dumps({'confirmed': confirmed}), content_type='application/json',
		)

	def test_ticking_confirms_the_calendar_date(self):
		self.assertEqual(self._post(True).status_code, 200)
		self.appointment.refresh_from_db()
		self.assertFalse(self.appointment.is_provisional)

	def test_unticking_puts_it_back_to_provisional(self):
		self.appointment.is_provisional = False
		self.appointment.save()
		self.assertEqual(self._post(False).status_code, 200)
		self.appointment.refresh_from_db()
		self.assertTrue(self.appointment.is_provisional)

	def test_the_tick_reflects_the_appointment(self):
		from .models import OrderWorkflowProgress, WorkflowStage
		from .views import _build_order_context
		stage = WorkflowStage.objects.create(
			name='Arrange Install Date & Take Stock Payment', phase='sale',
			role='customer-support', description='', order=1,
		)
		OrderWorkflowProgress.objects.create(order=self.order, current_stage=stage)
		request = RequestFactory().get('/')
		request.user = self.user
		request.session = self.client.session

		self.assertFalse(_build_order_context(self.order, request)['install_fit_date_booked'])
		self._post(True)
		self.assertTrue(_build_order_context(self.order, request)['install_fit_date_booked'])

	def test_cannot_confirm_without_an_appointment(self):
		self.appointment.delete()
		response = self._post(True)
		self.assertEqual(response.status_code, 400)
		self.assertIn('Book a fit date first', response.json()['error'])

	def test_get_is_rejected(self):
		self.assertEqual(
			self.client.get(reverse('set_order_fit_confirmed', args=[self.order.id])).status_code, 405
		)


class DashboardSalesCardTests(TestCase):
	"""The sales cards read the fit calendar, not raw AnthillSale.sale_value.

	Regression: every job fitting this week is still open (unbilled), so Anthill
	holds no sale_value for it and the cards reported £0.
	"""

	def setUp(self):
		from .models import FitAppointment
		self.client = Client()
		self.user = _create_user(is_superuser=True)
		self.user.profile.role = _create_role(name='admin')
		self.user.profile.save()
		self.client.login(username='testuser', password='testpass123')

		today = date.today()
		self.week_start = today - timedelta(days=today.weekday())
		self.order = _create_order(sale_number='500001', total_value_inc_vat=Decimal('12000.00'))
		FitAppointment.objects.create(order=self.order, fit_date=self.week_start, fitter='R')
		# The Anthill record exists but is unbilled — no value on it yet.
		AnthillSale.objects.create(
			anthill_activity_id='500001', contract_number='BFS-NR-500001',
			customer_name='Open Job', status='open', fit_date=self.week_start,
			sale_value=Decimal('0.00'),
		)

	def test_fit_jobs_values_an_unbilled_job_from_its_order(self):
		from .dashboard_view import _fit_jobs

		jobs = _fit_jobs(self.week_start, self.week_start + timedelta(days=6))
		self.assertEqual([j['value'] for j in jobs], [12000.0])

	def test_this_week_card_matches_the_targets_panel(self):
		response = self.client.get(reverse('dashboard'))
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context['this_week_sales'], '12,000')
		this_week = next(
			p for p in json.loads(response.context['targets_json']) if p['key'] == 'this_week'
		)
		self.assertEqual(this_week['actual'], 12000.0)

	def test_week_report_matches_the_card(self):
		response = self.client.get(reverse('dashboard_week_report'))
		self.assertEqual(response.json()['total'], 12000.0)

	def test_multi_day_appointments_count_once(self):
		from .models import FitAppointment
		from .dashboard_view import _fit_jobs

		FitAppointment.objects.create(
			order=self.order, fit_date=self.week_start + timedelta(days=1), fitter='R',
		)
		jobs = _fit_jobs(self.week_start, self.week_start + timedelta(days=6))
		self.assertEqual(len(jobs), 1)
		self.assertEqual(jobs[0]['date'], self.week_start)

	def test_duplicate_order_rows_for_one_contract_count_once(self):
		from .models import FitAppointment
		from .dashboard_view import _fit_jobs

		duplicate = _create_order(sale_number='500001', total_value_inc_vat=Decimal('12000.00'))
		FitAppointment.objects.create(order=duplicate, fit_date=self.week_start, fitter='D')
		jobs = _fit_jobs(self.week_start, self.week_start + timedelta(days=6))
		self.assertEqual([j['value'] for j in jobs], [12000.0])

	def test_billed_sale_without_a_calendar_entry_still_counts(self):
		from .dashboard_view import _fit_jobs

		AnthillSale.objects.create(
			anthill_activity_id='500002', contract_number='BFS-NR-500002',
			customer_name='Historic Job', status='completed', fit_date=self.week_start,
			sale_value=Decimal('4000.00'),
		)
		jobs = _fit_jobs(self.week_start, self.week_start + timedelta(days=6))
		self.assertEqual(sorted(j['value'] for j in jobs), [4000.0, 12000.0])

	def test_a_calendar_job_is_not_double_counted_by_its_anthill_sale(self):
		from .dashboard_view import _fit_jobs

		AnthillSale.objects.filter(anthill_activity_id='500001').update(sale_value=Decimal('11500.00'))
		jobs = _fit_jobs(self.week_start, self.week_start + timedelta(days=6))
		self.assertEqual(len(jobs), 1)


class CalendarPanelDuplicateOrderTests(TestCase):
	"""A sale_number can have more than one Order row; only the one linked from
	AnthillSale is canonical, and the calendar renders that one alone. The job
	panel must therefore only offer the canonical order — dragging a duplicate
	created an appointment the board immediately filtered out, so the drop
	looked like it silently did nothing."""

	def setUp(self):
		self.client = Client()
		self.user = _create_user(is_superuser=True)
		self.client.login(username='testuser', password='testpass123')
		Fitter.objects.create(code='R', name='Ross Middleton', active=True)
		# Duplicate pair: the orphan keeps a stale empty order_date so it looks
		# like a PFP job, while the canonical row has moved on.
		self.orphan = _create_order(
			sale_number='900001', customer_number='900001',
			first_name='Dup', last_name='Licate', order_date=None,
		)
		self.canonical = _create_order(
			sale_number='900001', customer_number='900001',
			first_name='Dup', last_name='Licate', order_date=date(2026, 5, 1),
		)
		AnthillSale.objects.create(
			anthill_activity_id='900001', customer_name='Dup Licate',
			order=self.canonical,
		)

	def test_job_panel_offers_the_canonical_order_not_the_duplicate(self):
		response = self.client.get(reverse('calendar_weekly'))
		self.assertEqual(response.status_code, 200)
		panel_ids = {
			o.id for o in
			list(response.context['pfp_orders']) + list(response.context['awaiting_orders'])
		}
		self.assertIn(self.canonical.id, panel_ids)
		self.assertNotIn(self.orphan.id, panel_ids)

	def test_dragging_a_duplicate_schedules_the_canonical_order(self):
		from .models import FitAppointment
		response = self.client.post(
			reverse('create_provisional_appointment'),
			data=json.dumps({
				'type': 'order', 'id': self.orphan.id,
				'fit_date': '2026-09-08', 'fitter': 'R', 'starts_pm': False,
			}),
			content_type='application/json',
		)
		self.assertTrue(json.loads(response.content)['success'])
		appointment = FitAppointment.objects.get(order=self.canonical)
		self.assertEqual(appointment.fit_date, date(2026, 9, 8))
		self.assertTrue(appointment.is_provisional)
		self.assertFalse(FitAppointment.objects.filter(order=self.orphan).exists())

	def test_the_dragged_job_then_appears_on_the_calendar(self):
		self.client.post(
			reverse('create_provisional_appointment'),
			data=json.dumps({
				'type': 'order', 'id': self.orphan.id,
				'fit_date': '2026-09-08', 'fitter': 'R', 'starts_pm': False,
			}),
			content_type='application/json',
		)
		response = self.client.get(reverse('calendar_weekly'), {'year': 2026, 'month': 9})
		shown = {
			entry['appt'].order_id
			for week in response.context['month_weeks']
			for lane in week['fitter_lanes']
			for entry in lane['appts']
		}
		self.assertIn(self.canonical.id, shown)


class PurchaseOrderListDateBandTests(TestCase):
	"""The PO list rows carry a normalised ordered date + total so the page can
	filter by date band and total the checked rows client-side."""

	def setUp(self):
		self.client = Client()
		self.user = _create_user(is_superuser=True)
		self.client.login(username='testuser', password='testpass123')
		# ISO with a UTC offset — the most common shape in the live data.
		self.iso_offset = PurchaseOrder.objects.create(
			workguru_id=9101, display_number='PO9101', status='Approved',
			supplier_name='Acme Timber', approved_date='2026-06-04T09:14:22+01:00',
			issue_date='2026-05-01', total=Decimal('1250.00'), currency='GBP',
		)
		# UK day-first with dashes — the shape the first cut of the filter
		# silently failed to parse, which is what broke the date band.
		self.uk_dashed = PurchaseOrder.objects.create(
			workguru_id=9102, display_number='PO9102', status='Approved',
			supplier_name='Beta Boards', approved_date='21-07-2026',
			issue_date='11/02/2026', total=Decimal('80.00'), currency='EUR',
		)
		# Never approved -> never ordered -> no ordered date.
		self.never_approved = PurchaseOrder.objects.create(
			workguru_id=9103, display_number='PO9103', status='Draft',
			supplier_name='Gamma Glass', issue_date='01/03/2026',
			total=Decimal('5.00'),
		)

	def _rows(self):
		response = self.client.get(reverse('purchase_orders_list'))
		self.assertEqual(response.status_code, 200)
		return {po.workguru_id: po for po in response.context['purchase_orders']}

	def test_ordered_date_parses_iso_with_offset(self):
		self.assertEqual(self._rows()[9101].ordered_date_iso, '2026-06-04')

	def test_ordered_date_parses_uk_day_first_dashed(self):
		self.assertEqual(self._rows()[9102].ordered_date_iso, '2026-07-21')

	def test_ordered_date_is_blank_when_never_approved(self):
		"""It must not fall back to issue_date — an unapproved PO wasn't ordered."""
		self.assertEqual(self._rows()[9103].ordered_date_iso, '')

	def test_rows_expose_total_and_currency_for_the_selection_total(self):
		html = self.client.get(reverse('purchase_orders_list')).content.decode()
		self.assertIn('data-total="1250.00"', html)
		self.assertIn('data-currency="EUR"', html)
		self.assertIn('class="po-row-check"', html)


class DateStrParsingTests(TestCase):
	"""The legacy CharField date columns hold four different real-world shapes;
	parse_date_str is the single place that reconciles them."""

	def test_parses_every_shape_present_in_the_data(self):
		from .date_utils import parse_date_str
		cases = {
			'2026-07-15T09:14:22+01:00': date(2026, 7, 15),
			'2026-07-15T09:14:22.1234567+01:00': date(2026, 7, 15),
			'2026-07-15': date(2026, 7, 15),
			'21-07-2026': date(2026, 7, 21),
			'21/07/2026': date(2026, 7, 21),
		}
		for raw, expected in cases.items():
			self.assertEqual(parse_date_str(raw), expected, msg=raw)

	def test_day_first_and_iso_are_not_confused(self):
		"""Both '05-06-2026' and '2026-06-05' are unambiguous by year position."""
		from .date_utils import parse_date_str
		self.assertEqual(parse_date_str('05-06-2026'), date(2026, 6, 5))
		self.assertEqual(parse_date_str('2026-06-05'), date(2026, 6, 5))

	def test_unparseable_and_impossible_values_return_none(self):
		from .date_utils import parse_date_str
		for raw in ('', None, 'n/a', 'not a date', '31-02-2026', '2026-13-01'):
			self.assertIsNone(parse_date_str(raw), msg=repr(raw))

	def test_date_objects_pass_through(self):
		from .date_utils import parse_date_str
		self.assertEqual(parse_date_str(date(2026, 7, 15)), date(2026, 7, 15))
		self.assertEqual(parse_date_str(datetime(2026, 7, 15, 9, 14)), date(2026, 7, 15))

	def test_display_filters_use_the_shared_parser(self):
		"""The DD-MM-YYYY shape used to fall through both filters unformatted."""
		from .templatetags.custom_filters import format_date_str, date_for_input
		self.assertEqual(format_date_str('21-07-2026'), '21/07/2026')
		self.assertEqual(date_for_input('21-07-2026'), '2026-07-21')
		self.assertEqual(format_date_str('2026-07-15T09:14:22+01:00'), '15/07/2026')
		self.assertEqual(date_for_input('2026-07-15T09:14:22+01:00'), '2026-07-15')
