"""
Unit tests for the stock_take application.

Run with: python manage.py test stock_take
"""
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.db.models import Sum
from django.test import TestCase, RequestFactory, Client, override_settings
from django.urls import reverse

from .models import (
    AnthillPayment, AnthillSale, Accessory, BoardsPO, Category, Customer,
    Expense, Fitter, Lead, OSDoor, Order, PNXItem, PagePermission,
    PurchaseOrder, Role, StockHistory, StockItem, Timesheet, UserProfile,
)
from .forms import BoardsPOForm, OrderForm
from .dashboard_view import _contract_prefix_for_location, _get_monthly_sales_data
from .permissions import get_user_permissions


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
        data = _get_monthly_sales_data(2025, 6, contract_prefix='BFS')
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['total'], 1000.0)


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
