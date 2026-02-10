from django.db import models
from django.utils import timezone
from decimal import Decimal
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

class Customer(models.Model):
    """Customer model to store customer information synced from WorkGuru"""
    # WorkGuru identifiers
    workguru_id = models.IntegerField(unique=True, null=True, blank=True, help_text='WorkGuru Client ID')
    
    # Legacy fields
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    anthill_customer_id = models.CharField(max_length=20, blank=True, help_text='Anthill CRM Customer ID')
    
    # Core details (from WorkGuru)
    name = models.CharField(max_length=255, blank=True, help_text='Client name from WorkGuru')
    code = models.CharField(max_length=50, blank=True, null=True, help_text='Client code')
    email = models.EmailField(max_length=254, blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    fax = models.CharField(max_length=50, blank=True, null=True)
    website = models.URLField(max_length=300, blank=True, null=True)
    abn = models.CharField(max_length=50, blank=True, null=True, help_text='Tax / ABN / VAT number')
    
    # Address fields
    address = models.CharField(max_length=255, blank=True)
    address_1 = models.CharField(max_length=255, blank=True, null=True)
    address_2 = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    state = models.CharField(max_length=100, blank=True, null=True)
    suburb = models.CharField(max_length=100, blank=True, null=True)
    postcode = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=100, blank=True, null=True)
    
    # Financial fields
    currency = models.CharField(max_length=10, blank=True, null=True)
    credit_days = models.CharField(max_length=20, blank=True, null=True)
    credit_limit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    credit_terms_type = models.CharField(max_length=50, blank=True, null=True)
    price_tier = models.CharField(max_length=100, blank=True, null=True)
    price_tier_id = models.IntegerField(null=True, blank=True)
    
    # Billing & templates
    billing_client = models.CharField(max_length=255, blank=True, null=True)
    billing_client_id = models.IntegerField(null=True, blank=True)
    default_invoice_template_id = models.IntegerField(null=True, blank=True)
    default_quote_template_id = models.IntegerField(null=True, blank=True)
    
    # Status
    is_active = models.BooleanField(default=True)
    xero_id = models.CharField(max_length=100, blank=True, null=True, help_text='Xero integration ID')
    
    # Metadata
    creation_time = models.DateTimeField(null=True, blank=True)
    last_modification_time = models.DateTimeField(null=True, blank=True)
    raw_data = models.JSONField(null=True, blank=True, help_text='Full raw API response')
    
    def __str__(self):
        if self.name:
            return self.name
        return f"{self.first_name} {self.last_name}".strip() or f"Customer #{self.pk}"
    
    class Meta:
        ordering = ['name', 'last_name', 'first_name']

class Designer(models.Model):
    """Designer model to store designer information"""
    name = models.CharField(max_length=100, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return self.name
    
    class Meta:
        ordering = ['name']

class BoardsPO(models.Model):
    po_number = models.CharField(max_length=50, unique=True)
    file = models.FileField(upload_to='boards_po_files/', blank=True, null=True)
    csv_file = models.FileField(upload_to='boards_po_files/', blank=True, null=True, help_text='CSV version of the PNX file')
    boards_ordered = models.BooleanField(default=False)

    def __str__(self):
        return self.po_number

    @property
    def boards_received(self):
        """Check if all PNX items have been fully received"""
        if not self.pnx_items.exists():
            return False
        return all(item.is_fully_received for item in self.pnx_items.all())


class Order(models.Model):
    # Customer link
    customer = models.ForeignKey('Customer', on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    
    # Legacy customer fields (will be deprecated)
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    address = models.CharField(max_length=255, blank=True)
    postcode = models.CharField(max_length=20, blank=True)
    anthill_id = models.CharField(max_length=20, blank=True, help_text='Anthill CRM Customer ID')
    
    # Order details
    sale_number = models.CharField(max_length=6)
    customer_number = models.CharField(max_length=6)
    order_date = models.DateField()
    fit_date = models.DateField()
    boards_po = models.ForeignKey(BoardsPO, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    job_finished = models.BooleanField(default=False)
    
    ORDER_TYPE_CHOICES = [
        ('sale', 'Sale'),
        ('remedial', 'Remedial'),
        ('warranty', 'Warranty'),
    ]
    order_type = models.CharField(max_length=20, choices=ORDER_TYPE_CHOICES, default='sale')
    designer = models.ForeignKey('Designer', on_delete=models.SET_NULL, null=True, blank=True, related_name='orders', help_text='Assigned designer')
    os_doors_required = models.BooleanField(default=False, help_text='True if OS Doors are required for this order')
    os_doors_po = models.CharField(max_length=50, blank=True, help_text='PO number when OS Doors are ordered')
    all_items_ordered = models.BooleanField(default=False, help_text='Manual confirmation that all items have been ordered')
    workguru_id = models.CharField(max_length=20, blank=True, help_text='WorkGuru Project ID')
    original_csv = models.FileField(upload_to='order_csvs/', blank=True, null=True, help_text='Original uploaded CSV file')
    processed_csv = models.FileField(upload_to='order_csvs/', blank=True, null=True, help_text='Processed CSV with substitutions applied')
    original_csv_uploaded_at = models.DateTimeField(blank=True, null=True, help_text='When the original CSV was uploaded')
    processed_csv_created_at = models.DateTimeField(blank=True, null=True, help_text='When the processed CSV was created')
    csv_has_missing_items = models.BooleanField(default=False, help_text='True if the uploaded CSV has unresolved missing items that need substitution')
    
    # Financial fields
    materials_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Cost of materials')
    installation_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Cost of installation')
    manufacturing_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Cost of manufacturing')
    total_value_inc_vat = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Total value including VAT')
    total_value_exc_vat = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Total value excluding VAT')
    profit = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Profit amount')
    fully_costed = models.BooleanField(default=False, help_text='Mark as fully costed for reporting')
    
    # Fit completion fields
    interior_completed = models.BooleanField(default=False, help_text='Interior fit completed')
    door_completed = models.BooleanField(default=False, help_text='Door fit completed')
    accessories_completed = models.BooleanField(default=False, help_text='Accessories fit completed')
    materials_completed = models.BooleanField(default=False, help_text='Materials delivered/ready')
    paperwork_completed = models.BooleanField(default=False, help_text='Paperwork completed')

    def time_allowance(self):
        return (self.fit_date - self.order_date).days

    def calculate_materials_cost(self, price_per_sqm=12):
        """Calculate total materials cost from boards, accessories, and OS doors"""
        total_cost = Decimal('0.00')
        
        # Add boards cost from PNX items (only for this order's sale number)
        if self.boards_po:
            # Filter PNX items by this order's sale_number in the customer field
            order_pnx_items = self.boards_po.pnx_items.filter(customer__icontains=self.sale_number)
            for pnx_item in order_pnx_items:
                total_cost += pnx_item.get_cost(price_per_sqm)
        
        # Add accessories cost
        for accessory in self.accessories.all():
            total_cost += accessory.cost_price * accessory.quantity
        
        # Add OS doors cost
        for os_door in self.os_doors.all():
            total_cost += os_door.cost_price * os_door.quantity
        
        return total_cost

    @property
    def all_materials_ordered(self):
        """Check if all materials for this order have been ordered"""
        # If manually marked as ordered, return True
        if self.all_items_ordered:
            return True
            
        # Check boards are ordered
        if not (self.boards_po and self.boards_po.boards_ordered):
            return False
        
        # Check OS doors are ordered (if required)
        if self.os_doors_required and not self.os_doors_po:
            return False
        
        # Check all accessories are ordered (if any exist)
        if self.accessories.exists():
            total_accessories = self.accessories.count()
            ordered_accessories = self.accessories.filter(ordered=True).count()
            if ordered_accessories != total_accessories:
                return False
        
        return True

    @property
    def order_boards_received(self):
        """Check if all boards for this specific order have been received"""
        if not self.boards_po:
            return False
        
        # Get PNX items for this order (same logic as in the view)
        order_pnx_items = self.boards_po.pnx_items.filter(customer__icontains=self.sale_number)
        
        if not order_pnx_items.exists():
            return False
        
        # Check if all PNX items for this order are fully received
        return all(item.is_fully_received for item in order_pnx_items)

    @property
    def os_doors_ordered(self):
        """Check if OS doors are ordered for this order"""
        return self.os_doors_required and bool(self.os_doors_po)

    @property
    def os_doors_received(self):
        """Check if all OS doors for this order have been received"""
        if not self.os_doors_required:
            return False
        
        if not self.os_doors.exists():
            return False
        
        # Check if all OS doors for this order are fully received
        return all(os_door.is_fully_received for os_door in self.os_doors.all())

    @property
    def has_missing_accessories(self):
        """Check if this order has any missing accessories"""
        return self.accessories.filter(missing=True).exists()
    
    def calculate_installation_cost(self):
        """Calculate installation cost from timesheets and expenses"""
        # Sum all installation timesheets
        timesheet_cost = sum(
            ts.total_cost for ts in self.timesheets.filter(timesheet_type='installation')
        )
        # Sum all expenses (petrol, materials, other)
        expense_cost = sum(
            exp.amount for exp in self.expenses.all()
        )
        return timesheet_cost + expense_cost
    
    def calculate_manufacturing_cost(self):
        """Calculate manufacturing cost from timesheets"""
        return sum(
            ts.total_cost for ts in self.timesheets.filter(timesheet_type='manufacturing')
        )


class PNXItem(models.Model):
    boards_po = models.ForeignKey(BoardsPO, on_delete=models.CASCADE, related_name='pnx_items')
    barcode = models.CharField(max_length=100)
    matname = models.CharField(max_length=100)
    cleng = models.DecimalField(max_digits=10, decimal_places=2)
    cwidth = models.DecimalField(max_digits=10, decimal_places=2)
    cnt = models.DecimalField(max_digits=10, decimal_places=2)
    customer = models.CharField(max_length=200)
    received = models.BooleanField(default=False)
    received_quantity = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Quantity that has been received')
    
    # Additional PNX fields
    grain = models.CharField(max_length=10, blank=True, default='')
    articlename = models.CharField(max_length=100, blank=True, default='')
    partdesc = models.CharField(max_length=200, blank=True, default='')
    prfid1 = models.CharField(max_length=100, blank=True, default='', help_text='Edge profile 1')
    prfid2 = models.CharField(max_length=100, blank=True, default='', help_text='Edge profile 2')
    prfid3 = models.CharField(max_length=100, blank=True, default='', help_text='Edge profile 3')
    prfid4 = models.CharField(max_length=100, blank=True, default='', help_text='Edge profile 4')
    ordername = models.CharField(max_length=100, blank=True, default='', help_text='Order/Sale number from PNX')

    # Price per square meter for boards
    PRICE_PER_SQM = 50

    class Meta:
        ordering = ['barcode', 'matname', 'customer']

    def __str__(self):
        return f"{self.barcode} - {self.matname}"

    @property
    def is_fully_received(self):
        """Check if the item is fully received"""
        return self.received_quantity >= self.cnt

    @property
    def is_partially_received(self):
        """Check if the item is partially received"""
        return self.received_quantity > 0 and self.received_quantity < self.cnt

    def get_cost(self, price_per_sqm=None):
        """Calculate cost based on dimensions and count"""
        if price_per_sqm is None:
            price_per_sqm = self.PRICE_PER_SQM
        
        # Convert price to Decimal for consistent calculations
        price_per_sqm = Decimal(str(price_per_sqm))
        
        # Convert mm to meters
        length_m = self.cleng / 1000
        width_m = self.cwidth / 1000
        
        # Calculate area in square meters
        area_sqm = length_m * width_m
        
        # Multiply by count and price per sqm
        return area_sqm * self.cnt * price_per_sqm


class OSDoor(models.Model):
    customer = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='os_doors')
    door_style = models.CharField(max_length=100)
    style_colour = models.CharField(max_length=100)
    item_description = models.TextField()
    height = models.DecimalField(max_digits=6, decimal_places=2)
    width = models.DecimalField(max_digits=6, decimal_places=2)
    colour = models.CharField(max_length=100)
    quantity = models.PositiveIntegerField()
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Cost price per door')
    ordered = models.BooleanField(default=False)
    received = models.BooleanField(default=False)
    received_quantity = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Quantity that has been received')
    po_number = models.CharField(max_length=100, blank=True, null=True, help_text='PO Number for tracking OS Doors orders')

    def __str__(self):
        return f"OS Door for {self.customer.sale_number} - {self.door_style}"

    @property
    def is_fully_received(self):
        """Check if the item is fully received"""
        return self.received_quantity >= self.quantity

    @property
    def is_partially_received(self):
        """Check if the item is partially received"""
        return self.received_quantity > 0 and self.received_quantity < self.quantity


class Accessory(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='accessories')
    sku = models.CharField(max_length=100)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    sell_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    billable = models.BooleanField(default=True)
    stock_item = models.ForeignKey('StockItem', on_delete=models.SET_NULL, null=True, blank=True, related_name='accessories')
    is_os_door = models.BooleanField(default=False, help_text='True if this is an OS Door accessory (DOR_VNL_OSD_MTM)')
    required = models.BooleanField(default=False, help_text='Required for OS Doors')
    ordered = models.BooleanField(default=False, help_text='Ordered for OS Doors')
    missing = models.BooleanField(default=False, help_text='True if SKU not found in stock')

    @property
    def available_quantity(self):
        """Get available quantity from linked stock item"""
        if self.stock_item:
            return self.stock_item.quantity
        return 0

    @property
    def allocated_quantity(self):
        """Get quantity allocated to other non-completed jobs"""
        from django.db.models import Sum
        # Get all accessories with same SKU, excluding current order and completed jobs
        allocated = Accessory.objects.filter(
            sku=self.sku,
            order__job_finished=False
        ).exclude(
            order=self.order
        ).aggregate(total=Sum('quantity'))['total'] or 0
        return allocated

    def __str__(self):
        return f"{self.sku} - {self.name} ({self.order.sale_number})"


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    color = models.CharField(max_length=7, default='#6c757d', help_text='Hex color code')
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='subcategories')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'Categories'
        ordering = ['name']

    def __str__(self):
        if self.parent:
            return f"{self.parent.name} > {self.name}"
        return self.name

    @property
    def is_parent(self):
        return self.subcategories.exists()

    @property
    def full_path(self):
        if self.parent:
            return f"{self.parent.name} > {self.name}"
        return self.name


class StockTakeGroup(models.Model):
    """Subcategories for organizing stock takes with priority weighting"""
    WEIGHTING_CHOICES = [
        (1, 'Low Priority'),
        (2, 'Medium Priority'),
        (3, 'High Priority'),
        (4, 'Critical Priority'),
    ]
    
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='stock_take_groups')
    weighting = models.IntegerField(choices=WEIGHTING_CHOICES, default=2, 
                                  help_text='Higher weighting = more frequent stock takes needed')
    color = models.CharField(max_length=7, default='#6c757d')
    auto_schedule_threshold = models.IntegerField(default=5, 
                                                help_text='Auto-create stock take when items drop below this quantity')
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['category', 'name']
        ordering = ['-weighting', 'name']
    
    def __str__(self):
        return f"{self.category.name} - {self.name}"
    
    @property
    def items_needing_check(self):
        """Get items that need stock checking based on threshold"""
        return self.stock_items.filter(quantity__lte=self.auto_schedule_threshold)
    
    @property
    def priority_label(self):
        return dict(self.WEIGHTING_CHOICES)[self.weighting]


class StockItem(models.Model):
    TRACKING_CHOICES = [
        ('stock', 'Stock'),
        ('non-stock', 'Non-Stock'),
        ('not-classified', 'Not-Classified'),
    ]

    sku = models.CharField(max_length=100, db_index=True)
    name = models.CharField(max_length=200, db_index=True)
    cost = models.DecimalField(max_digits=10, decimal_places=2)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    stock_take_group = models.ForeignKey(StockTakeGroup, on_delete=models.SET_NULL, 
                                       null=True, blank=True, related_name='stock_items')
    category_name = models.CharField(max_length=100, blank=True)  # For CSV compatibility
    location = models.CharField(max_length=100)
    quantity = models.IntegerField(db_index=True)
    serial_or_batch = models.CharField(max_length=100, blank=True, null=True)
    last_checked = models.DateTimeField(null=True, blank=True)
    tracking_type = models.CharField(max_length=30, choices=TRACKING_CHOICES, default='not-classified', db_index=True)
    min_order_qty = models.IntegerField(blank=True, null=True)
    par_level = models.IntegerField(default=0, help_text='Minimum stock level - alerts when stock falls below this')
    
    class Meta:
        indexes = [
            models.Index(fields=['tracking_type', 'quantity']),
            models.Index(fields=['category', 'tracking_type']),
        ]
    
    @property
    def total_value(self):
        return self.cost * self.quantity
    
    @property
    def needs_stock_take(self):
        """Check if item needs stock take based on group weighting and thresholds"""
        if not self.stock_take_group:
            return False
        return self.quantity <= self.stock_take_group.auto_schedule_threshold
    
    def __str__(self):
        return f"{self.sku} - {self.name}"


class StockHistory(models.Model):
    """Track stock level changes over time for graphing and analysis"""
    stock_item = models.ForeignKey(StockItem, on_delete=models.CASCADE, related_name='stock_history')
    quantity = models.IntegerField(help_text='Stock quantity at this point in time')
    change_amount = models.IntegerField(help_text='Amount changed (positive for additions, negative for usage)')
    change_type = models.CharField(max_length=50, choices=[
        ('stock_take', 'Stock Take'),
        ('purchase', 'Purchase Order'),
        ('sale', 'Sale/Usage'),
        ('adjustment', 'Manual Adjustment'),
        ('initial', 'Initial Stock'),
    ], default='adjustment')
    reference = models.CharField(max_length=100, blank=True, help_text='PO number, order number, etc.')
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    created_by = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['stock_item', '-created_at']),
        ]
    
    def __str__(self):
        return f"{self.stock_item.sku} - {self.quantity} units ({self.created_at.strftime('%Y-%m-%d')})"


class Supplier(models.Model):
    """Local copy of WorkGuru Suppliers - extracted from PO details"""
    workguru_id = models.IntegerField(unique=True, help_text='WorkGuru Supplier ID')
    name = models.CharField(max_length=255)
    
    # Contact info
    email = models.CharField(max_length=255, blank=True, null=True)
    phone = models.CharField(max_length=100, blank=True, null=True)
    website = models.CharField(max_length=255, blank=True, null=True)
    
    # Address
    address_1 = models.CharField(max_length=255, blank=True, null=True)
    address_2 = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    state = models.CharField(max_length=100, blank=True, null=True)
    postcode = models.CharField(max_length=20, blank=True, null=True)
    country = models.CharField(max_length=10, blank=True, null=True)
    
    # Financial
    currency = models.CharField(max_length=10, blank=True, null=True)
    credit_limit = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    credit_days = models.CharField(max_length=20, blank=True, null=True)
    
    # Status
    is_active = models.BooleanField(default=True)
    
    # Tracking
    last_synced = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    raw_data = models.JSONField(null=True, blank=True)
    
    class Meta:
        ordering = ['name']
        indexes = [
            models.Index(fields=['workguru_id']),
            models.Index(fields=['name']),
        ]
    
    def __str__(self):
        return self.name


class PurchaseOrder(models.Model):
    """Local copy of WorkGuru Purchase Orders"""
    workguru_id = models.IntegerField(unique=True, help_text='WorkGuru PO ID')
    number = models.CharField(max_length=50, blank=True, null=True)
    display_number = models.CharField(max_length=50, blank=True, null=True)
    revision = models.IntegerField(default=0)
    description = models.TextField(blank=True, null=True)
    
    # Project/Customer
    project_id = models.IntegerField(null=True, blank=True)
    project_number = models.CharField(max_length=100, blank=True, null=True)
    project_name = models.CharField(max_length=200, blank=True, null=True)
    
    # Supplier
    supplier_id = models.IntegerField(null=True, blank=True)
    supplier_name = models.CharField(max_length=200, blank=True, null=True)
    supplier_invoice_number = models.CharField(max_length=100, blank=True, null=True)
    
    # Dates
    issue_date = models.CharField(max_length=20, blank=True, null=True)
    expected_date = models.CharField(max_length=20, blank=True, null=True)
    received_date = models.CharField(max_length=20, blank=True, null=True)
    invoice_date = models.CharField(max_length=20, blank=True, null=True)
    
    # Status and Financials
    status = models.CharField(max_length=50, default='Draft', blank=True, null=True)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    forecast_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    base_currency_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    currency = models.CharField(max_length=10, default='GBP', blank=True, null=True)
    exchange_rate = models.DecimalField(max_digits=10, decimal_places=4, default=1.0)
    
    # Delivery
    warehouse_id = models.IntegerField(null=True, blank=True)
    delivery_address_1 = models.CharField(max_length=255, blank=True, null=True)
    delivery_address_2 = models.CharField(max_length=255, blank=True, null=True)
    delivery_instructions = models.TextField(blank=True, null=True)
    
    # Flags
    sent_to_supplier = models.CharField(max_length=50, blank=True, null=True)
    sent_to_accounting = models.CharField(max_length=50, blank=True, null=True)
    billable = models.BooleanField(default=False)
    is_advanced = models.BooleanField(default=False)
    is_rfq = models.BooleanField(default=False)
    
    # Metadata
    creator_name = models.CharField(max_length=200, blank=True, null=True)
    received_by_name = models.CharField(max_length=200, blank=True, null=True)
    
    # Local tracking
    last_synced = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    # Raw JSON data for reference
    raw_data = models.JSONField(null=True, blank=True, help_text='Full JSON from WorkGuru API')
    
    class Meta:
        ordering = ['-workguru_id']
        indexes = [
            models.Index(fields=['workguru_id']),
            models.Index(fields=['number']),
            models.Index(fields=['status']),
        ]
    
    def __str__(self):
        return f"{self.display_number} - {self.supplier_name}"


class PurchaseOrderProduct(models.Model):
    """Products/line items in a purchase order"""
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='products')
    sku = models.CharField(max_length=100, blank=True)
    supplier_code = models.CharField(max_length=100, blank=True)
    name = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    
    order_price = models.DecimalField(max_digits=10, decimal_places=5, default=0)
    order_quantity = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    received_quantity = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    invoice_price = models.DecimalField(max_digits=10, decimal_places=5, default=0)
    line_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    # Link to local stock item if available
    stock_item = models.ForeignKey(StockItem, on_delete=models.SET_NULL, null=True, blank=True, related_name='purchase_order_lines')
    
    def __str__(self):
        return f"{self.purchase_order.display_number} - {self.sku} - {self.name}"


class Remedial(models.Model):
    """Remedial work orders linked to original orders"""
    original_order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='remedials')
    remedial_number = models.CharField(max_length=20, unique=True, help_text='Unique remedial reference number')
    reason = models.TextField(help_text='Reason for remedial work')
    notes = models.TextField(blank=True, help_text='Additional notes')
    
    # Order details (can override original order details)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    customer_number = models.CharField(max_length=6)
    address = models.CharField(max_length=255, blank=True)
    postcode = models.CharField(max_length=20, blank=True)
    
    # Scheduling
    created_date = models.DateField(auto_now_add=True)
    scheduled_date = models.DateField(null=True, blank=True, help_text='Date remedial work is scheduled')
    completed_date = models.DateField(null=True, blank=True, help_text='Date remedial was completed')
    
    # Materials
    boards_po = models.ForeignKey(BoardsPO, on_delete=models.SET_NULL, null=True, blank=True, related_name='remedials')
    os_doors_required = models.BooleanField(default=False)
    os_doors_po = models.CharField(max_length=50, blank=True)
    
    # Status
    is_completed = models.BooleanField(default=False)
    all_items_ordered = models.BooleanField(default=False)
    
    # External IDs
    anthill_id = models.CharField(max_length=20, blank=True)
    workguru_id = models.CharField(max_length=20, blank=True)
    
    class Meta:
        ordering = ['-created_date']
    
    def __str__(self):
        return f"{self.remedial_number} - {self.first_name} {self.last_name}"
    
    @property
    def days_since_created(self):
        """Calculate days since remedial was created"""
        if self.created_date:
            return (timezone.now().date() - self.created_date).days
        return 0
    
    @property
    def is_overdue(self):
        """Check if scheduled date has passed and not completed"""
        if self.scheduled_date and not self.is_completed:
            return timezone.now().date() > self.scheduled_date
        return False


class RemedialAccessory(models.Model):
    """Accessories needed for remedial work"""
    remedial = models.ForeignKey(Remedial, on_delete=models.CASCADE, related_name='accessories')
    sku = models.CharField(max_length=100)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    stock_item = models.ForeignKey(StockItem, on_delete=models.SET_NULL, null=True, blank=True)
    ordered = models.BooleanField(default=False)
    received = models.BooleanField(default=False)
    
    def __str__(self):
        return f"{self.sku} - {self.name} (Remedial: {self.remedial.remedial_number})"
    
    @property
    def available_quantity(self):
        """Get available quantity from linked stock item"""
        if self.stock_item:
            return self.stock_item.quantity
        return 0


class Schedule(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
    ]
    
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    stock_take_groups = models.ManyToManyField(StockTakeGroup, blank=True)
    locations = models.TextField(help_text='Comma-separated list of locations')
    scheduled_date = models.DateTimeField()
    created_date = models.DateTimeField(auto_now_add=True)
    completed_date = models.DateTimeField(blank=True, null=True, help_text='Date when the schedule was marked as completed')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    assigned_to = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    auto_generated = models.BooleanField(default=False, help_text='Auto-created based on stock levels')
    
    class Meta:
        ordering = ['scheduled_date']
    
    def __str__(self):
        return f"{self.name} - {self.scheduled_date.strftime('%Y-%m-%d')}"
    
    @property
    def is_overdue(self):
        return self.scheduled_date < timezone.now() and self.status != 'completed'
    
    @property
    def priority_score(self):
        """Calculate priority based on stock take groups weighting"""
        return sum(group.weighting for group in self.stock_take_groups.all())


class ImportHistory(models.Model):
    imported_at = models.DateTimeField(default=timezone.now)
    filename = models.CharField(max_length=255)
    record_count = models.IntegerField()
    
    class Meta:
        ordering = ['-imported_at']
    
    def __str__(self):
        return f"Import on {self.imported_at} - {self.record_count} records"


class Substitution(models.Model):
    missing_sku = models.CharField(max_length=100)
    missing_name = models.CharField(max_length=255)
    replacement_sku = models.CharField(max_length=100)
    replacement_name = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    sell_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    quantity = models.IntegerField(null=True, blank=True)
    billable = models.BooleanField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.missing_name} -> {self.replacement_name}"


class CSVSkipItem(models.Model):
    """Items to skip/remove during CSV processing and resolution"""
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='csv_skip_items', null=True, blank=True)
    sku = models.CharField(max_length=100)
    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        # Prevent duplicate SKUs per order, and globally for null orders
        constraints = [
            models.UniqueConstraint(fields=['order', 'sku'], name='unique_order_sku_skipitem'),
            models.UniqueConstraint(fields=['sku'], condition=models.Q(order__isnull=True), name='unique_global_sku_skipitem')
        ]
    
    def __str__(self):
        return f"{self.sku} - {self.name}"


class FitAppointment(models.Model):
    """Track fit appointments and completion status"""
    FITTER_CHOICES = [
        ('R', 'Ross'),
        ('G', 'Gavin'),
        ('S', 'Stuart'),
        ('P', 'Paddy'),
    ]
    
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='fit_appointments', null=True, blank=True)
    remedial = models.ForeignKey(Remedial, on_delete=models.CASCADE, related_name='fit_appointments', null=True, blank=True)
    fit_date = models.DateField(help_text='Scheduled fit date')
    fitter = models.CharField(max_length=1, choices=FITTER_CHOICES, default='R', help_text='Assigned fitter')
    interior_completed = models.BooleanField(default=False, help_text='Interior fit completed')
    door_completed = models.BooleanField(default=False, help_text='Door fit completed')
    accessories_completed = models.BooleanField(default=False, help_text='Accessories fit completed')
    materials_completed = models.BooleanField(default=False, help_text='Materials delivered/ready')
    notes = models.TextField(blank=True, help_text='Additional notes about the fit')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['fit_date', 'fitter', 'order__last_name']
    
    def __str__(self):
        if self.order:
            return f"{self.get_fitter_display()} - {self.order.first_name} {self.order.last_name} - {self.fit_date}"
        elif self.remedial:
            return f"{self.get_fitter_display()} - {self.remedial.remedial_number} - {self.fit_date}"
        return f"{self.get_fitter_display()} - {self.fit_date}"
    
    @property
    def is_fully_completed(self):
        """Check if all aspects of the fit are completed"""
        return self.interior_completed and self.door_completed and self.accessories_completed and self.materials_completed
    
    @property
    def customer_name(self):
        """Get full customer name"""
        if self.order:
            return f"{self.order.first_name} {self.order.last_name}"
        elif self.remedial:
            return f"{self.remedial.remedial_number} - {self.remedial.first_name} {self.remedial.last_name}"
        return "Unknown"


class WorkflowStage(models.Model):
    """Defines a stage in the customer workflow process"""
    PHASE_CHOICES = [
        ('enquiry', 'Enquiry'),
        ('lead', 'Lead'),
        ('sale', 'Sale'),
    ]
    
    ROLE_CHOICES = [
        ('customer-support', 'Customer Support'),
        ('design', 'Design'),
        ('fitter', 'Fitter'),
        ('operations', 'Operations'),
        ('manufacturing', 'Manufacturing'),
        ('enquiry', 'Enquiry'),
        ('waiting', 'Waiting Period'),
    ]
    
    name = models.CharField(max_length=200, help_text='Name of the workflow stage')
    phase = models.CharField(max_length=20, choices=PHASE_CHOICES, help_text='Which phase this stage belongs to')
    role = models.CharField(max_length=30, choices=ROLE_CHOICES, help_text='Which role is responsible for this stage')
    description = models.TextField(help_text='Description of what needs to be done in this stage')
    expected_days = models.IntegerField(null=True, blank=True, help_text='Expected number of days for this stage')
    order = models.IntegerField(default=0, help_text='Order in which stages appear')
    
    class Meta:
        ordering = ['order', 'phase']
    
    def __str__(self):
        return f"{self.phase.upper()} - {self.name}"


class WorkflowTask(models.Model):
    """Individual tasks/checkboxes within a workflow stage"""
    TASK_TYPE_CHOICES = [
        ('record', 'Record Checkbox'),
        ('requirement', 'Requirement Checkbox'),
        ('attachment', 'Attachment Field'),
        ('radio', 'Radio Buttons'),
        ('dropdown', 'Dropdown Menu'),
        ('decision_matrix', 'Decision Matrix'),
    ]
    
    stage = models.ForeignKey(WorkflowStage, on_delete=models.CASCADE, related_name='tasks')
    description = models.CharField(max_length=300, help_text='Description of the task')
    task_type = models.CharField(max_length=20, choices=TASK_TYPE_CHOICES, default='record', help_text='Type of task')
    options = models.TextField(blank=True, help_text='Comma-separated options for radio/dropdown (e.g., "Brochure,Design Appointment,Both")')
    order = models.IntegerField(default=0, help_text='Order in which tasks appear')
    
    class Meta:
        ordering = ['order']
    
    def __str__(self):
        return f"{self.stage.name} - {self.description}"


class OrderWorkflowProgress(models.Model):
    """Tracks which workflow stage an order is currently in and task completion"""
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name='workflow_progress')
    current_stage = models.ForeignKey(WorkflowStage, on_delete=models.SET_NULL, null=True, related_name='orders_in_stage')
    stage_started_at = models.DateTimeField(auto_now_add=True)
    stage_updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['order']
    
    def __str__(self):
        return f"{self.order.sale_number} - {self.current_stage.name if self.current_stage else 'No Stage'}"
    
    @property
    def can_progress_to_next_stage(self):
        """Check if all requirement tasks are completed"""
        if not self.current_stage:
            return True
        
        # Get all requirement tasks for current stage
        requirement_tasks = self.current_stage.tasks.filter(task_type='requirement')
        if not requirement_tasks.exists():
            return True
        
        # Check if all requirement tasks are completed
        for task in requirement_tasks:
            completion = self.task_completions.filter(task=task).first()
            if not completion or not completion.completed:
                return False
        
        return True


class TaskCompletion(models.Model):
    """Tracks completion of individual tasks within a workflow stage for an order"""
    order_progress = models.ForeignKey(OrderWorkflowProgress, on_delete=models.CASCADE, related_name='task_completions')
    task = models.ForeignKey(WorkflowTask, on_delete=models.CASCADE)
    completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.CharField(max_length=100, blank=True, help_text='User who completed the task')
    attachment = models.FileField(upload_to='workflow_attachments/', null=True, blank=True, help_text='File attachment for this task')
    selected_option = models.CharField(max_length=200, blank=True, help_text='Selected option for radio/dropdown tasks')
    notes = models.TextField(blank=True, help_text='Additional notes for this task completion')
    
    class Meta:
        unique_together = ['order_progress', 'task']
        ordering = ['task__order']
    
    def __str__(self):
        status = '✓' if self.completed else '○'
        return f"{status} {self.order_progress.order.sale_number} - {self.task.description}"


class Fitter(models.Model):
    """Model for installation fitters"""
    name = models.CharField(max_length=100)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    hourly_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Hourly rate for this fitter')
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return self.name
    
    class Meta:
        ordering = ['name']


class FactoryWorker(models.Model):
    """Model for factory/manufacturing workers"""
    name = models.CharField(max_length=100)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    hourly_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Hourly rate for this worker')
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return self.name
    
    class Meta:
        ordering = ['name']


class Timesheet(models.Model):
    """Timesheet entries for both fitters and factory workers"""
    TIMESHEET_TYPE_CHOICES = [
        ('installation', 'Installation'),
        ('manufacturing', 'Manufacturing'),
    ]
    
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='timesheets')
    timesheet_type = models.CharField(max_length=20, choices=TIMESHEET_TYPE_CHOICES)
    
    # Worker - use either fitter, factory_worker, or helper (for installation additional party)
    fitter = models.ForeignKey(Fitter, on_delete=models.SET_NULL, null=True, blank=True, related_name='timesheets')
    factory_worker = models.ForeignKey(FactoryWorker, on_delete=models.SET_NULL, null=True, blank=True, related_name='timesheets')
    helper = models.ForeignKey(Fitter, on_delete=models.SET_NULL, null=True, blank=True, related_name='helper_timesheets', help_text='Additional party for installation')
    
    date = models.DateField()
    
    # For installation timesheets (fixed price)
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text='Fixed price for installation')
    
    # For manufacturing timesheets (hours × rate)
    hours = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, help_text='Hours worked')
    hourly_rate = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text='Rate at time of entry')
    
    description = models.TextField(blank=True, help_text='Description of work performed')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    @property
    def worker_name(self):
        """Get the worker name regardless of type"""
        if self.fitter:
            return self.fitter.name
        elif self.factory_worker:
            return self.factory_worker.name
        elif self.helper:
            return self.helper.name
        return 'Unknown'
    
    @property
    def worker_type(self):
        """Get the worker type"""
        if self.fitter:
            return 'fitter'
        elif self.factory_worker:
            return 'factory_worker'
        elif self.helper:
            return 'helper'
        return 'unknown'
    
    @property
    def total_cost(self):
        """Calculate total cost for this timesheet entry"""
        if self.timesheet_type == 'installation' and self.price:
            # Installation uses fixed price
            return self.price
        elif self.hours and self.hourly_rate:
            # Manufacturing uses hours × hourly_rate
            return self.hours * self.hourly_rate
        return 0
    
    def __str__(self):
        if self.timesheet_type == 'installation':
            return f"{self.worker_name} - {self.date} (£{self.price})"
        return f"{self.worker_name} - {self.date} ({self.hours}h)"
    
    class Meta:
        ordering = ['-date', '-created_at']


class Expense(models.Model):
    """Expense entries for fitters (e.g., petrol, materials)"""
    EXPENSE_TYPE_CHOICES = [
        ('petrol', 'Petrol'),
        ('materials', 'Materials'),
        ('other', 'Other'),
    ]
    
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='expenses')
    fitter = models.ForeignKey(Fitter, on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses')
    
    expense_type = models.CharField(max_length=20, choices=EXPENSE_TYPE_CHOICES, default='petrol')
    date = models.DateField()
    amount = models.DecimalField(max_digits=10, decimal_places=2, help_text='Expense amount')
    description = models.TextField(blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        fitter_name = self.fitter.name if self.fitter else 'Unknown'
        return f"{fitter_name} - {self.expense_type} - £{self.amount}"
    
    class Meta:
        ordering = ['-date', '-created_at']


class UserProfile(models.Model):
    """User profile to store user preferences"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    dark_mode = models.BooleanField(default=True, help_text='Enable dark mode theme')
    
    def __str__(self):
        return f"{self.user.username}'s profile"


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """Create a UserProfile when a new User is created"""
    if created:
        UserProfile.objects.create(user=instance)


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    """Save the UserProfile when the User is saved"""
    if hasattr(instance, 'profile'):
        instance.profile.save()
