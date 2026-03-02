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
    title = models.CharField(max_length=20, blank=True, null=True, help_text='Title e.g. Mr, Mrs, Dr')
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    anthill_customer_id = models.CharField(max_length=20, blank=True, help_text='Anthill CRM Customer ID')
    
    # Core details (from WorkGuru)
    name = models.CharField(max_length=255, blank=True, help_text='Client name from WorkGuru')
    code = models.CharField(max_length=100, blank=True, null=True, help_text='Client code')
    email = models.EmailField(max_length=254, blank=True, null=True)
    phone = models.CharField(max_length=100, blank=True, null=True)
    fax = models.CharField(max_length=100, blank=True, null=True)
    website = models.URLField(max_length=300, blank=True, null=True)
    abn = models.CharField(max_length=100, blank=True, null=True, help_text='Tax / ABN / VAT number')
    
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
    credit_terms_type = models.CharField(max_length=100, blank=True, null=True)
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
    
    # Anthill CRM
    anthill_created_date = models.DateTimeField(null=True, blank=True, help_text='When this customer was created in Anthill CRM')
    location = models.CharField(max_length=100, blank=True, null=True, help_text='Anthill location / branch')
    
    # Metadata
    creation_time = models.DateTimeField(null=True, blank=True)
    last_modification_time = models.DateTimeField(null=True, blank=True)
    raw_data = models.JSONField(null=True, blank=True, help_text='Full raw API response')
    
    def __str__(self):
        if self.name:
            return self.name
        return f"{self.first_name} {self.last_name}".strip() or f"Customer #{self.pk}"
    
    @property
    def url_name(self):
        """Return customer name formatted for use in URLs (spaces replaced with +)"""
        name = self.name or f"{self.first_name} {self.last_name}".strip()
        return name.replace(' ', '+') if name else str(self.pk)
    
    class Meta:
        ordering = ['name', 'last_name', 'first_name']


class Lead(models.Model):
    """Lead model to track potential customers / sales leads"""

    STATUS_CHOICES = [
        ('new', 'New'),
        ('contacted', 'Contacted'),
        ('qualified', 'Qualified'),
        ('proposal', 'Proposal'),
        ('converted', 'Converted'),
        ('lost', 'Lost'),
    ]

    SOURCE_CHOICES = [
        ('website', 'Website'),
        ('referral', 'Referral'),
        ('social_media', 'Social Media'),
        ('phone', 'Phone'),
        ('email', 'Email'),
        ('walk_in', 'Walk-In'),
        ('advertisement', 'Advertisement'),
        ('anthill', 'Anthill CRM'),
        ('other', 'Other'),
    ]

    # Core details
    name = models.CharField(max_length=255, help_text='Lead / contact name')
    email = models.EmailField(max_length=254, blank=True, null=True)
    phone = models.CharField(max_length=100, blank=True, null=True)
    mobile = models.CharField(max_length=100, blank=True, null=True)
    website = models.URLField(max_length=300, blank=True, null=True)

    # Address fields
    address_1 = models.CharField(max_length=255, blank=True, null=True)
    address_2 = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    state = models.CharField(max_length=100, blank=True, null=True)
    postcode = models.CharField(max_length=20, blank=True, null=True)
    country = models.CharField(max_length=100, blank=True, null=True)

    # Lead-specific fields
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new')
    source = models.CharField(max_length=30, choices=SOURCE_CHOICES, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    value = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text='Estimated value')

    # Conversion
    converted_to_customer = models.ForeignKey(
        Customer, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='source_leads', help_text='Customer created from this lead'
    )

    # Anthill CRM
    anthill_customer_id = models.CharField(max_length=20, blank=True, null=True, unique=True, help_text='Anthill CRM Customer ID')
    anthill_created_date = models.DateTimeField(null=True, blank=True, help_text='When this lead was created in Anthill CRM')
    location = models.CharField(max_length=100, blank=True, null=True, help_text='Anthill location / branch')
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name or f"Lead #{self.pk}"

    class Meta:
        ordering = ['-created_at']


class AnthillSale(models.Model):
    """Sale activity from Anthill CRM, linked to a Customer."""

    # Anthill identifiers
    anthill_activity_id = models.CharField(max_length=30, unique=True, help_text='Anthill activity ID')
    anthill_customer_id = models.CharField(max_length=20, blank=True, db_index=True, help_text='Anthill customer ID this sale belongs to')

    # Link to local customer (nullable until we can match)
    customer = models.ForeignKey(
        Customer, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='anthill_sales', help_text='Linked local customer record'
    )

    # Link to local order if one exists
    order = models.ForeignKey(
        'Order', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='anthill_sale', help_text='Linked local order if one exists'
    )

    # Sale info from Anthill
    activity_type = models.CharField(max_length=100, blank=True, help_text='Activity type e.g. "Sale"')
    status = models.CharField(max_length=50, blank=True, help_text='Activity status e.g. "Complete"')
    category = models.CharField(max_length=100, blank=True)
    customer_name = models.CharField(max_length=255, blank=True, help_text='Customer name from Anthill')
    location = models.CharField(max_length=100, blank=True, null=True)

    # Dates
    activity_date = models.DateTimeField(null=True, blank=True, help_text='When this activity was created in Anthill')

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Sale {self.anthill_activity_id} - {self.customer_name or 'Unknown'}"

    class Meta:
        ordering = ['-activity_date']
        verbose_name = 'Anthill Sale'
        verbose_name_plural = 'Anthill Sales'


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
    order_date = models.DateField(null=True, blank=True)
    fit_date = models.DateField(null=True, blank=True)
    boards_po = models.ForeignKey(BoardsPO, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    additional_boards_pos = models.ManyToManyField(BoardsPO, blank=True, related_name='additional_orders', help_text='Additional boards POs for this order')
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
        if not self.fit_date or not self.order_date:
            return None
        return (self.fit_date - self.order_date).days

    @property
    def all_boards_pos(self):
        """Return a list of all boards POs (primary + additional)."""
        pos = []
        if self.boards_po:
            pos.append(self.boards_po)
        pos.extend(self.additional_boards_pos.all())
        return pos

    def calculate_materials_cost(self, price_per_sqm=12):
        """Calculate total materials cost from boards, accessories, and OS doors"""
        total_cost = Decimal('0.00')
        
        # Add boards cost from PNX items across ALL boards POs
        for bpo in self.all_boards_pos:
            order_pnx_items = bpo.pnx_items.filter(customer__icontains=self.sale_number)
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
            
        # Check ALL boards POs are ordered
        all_pos = self.all_boards_pos
        if not all_pos:
            return False
        for bpo in all_pos:
            if not bpo.boards_ordered:
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
        all_pos = self.all_boards_pos
        if not all_pos:
            return False
        
        # Check PNX items across ALL boards POs
        has_items = False
        for bpo in all_pos:
            order_pnx_items = bpo.pnx_items.filter(customer__icontains=self.sale_number)
            if order_pnx_items.exists():
                has_items = True
                if not all(item.is_fully_received for item in order_pnx_items):
                    return False
        
        return has_items

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
    is_allocated = models.BooleanField(default=False, help_text='True if stock has been physically used/deducted')
    
    # Cut-to-size dimensions (for glass items)
    cut_width = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text='Cut-to-size width in mm')
    cut_height = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text='Cut-to-size height in mm')

    @property
    def is_cut_to_size(self):
        """Check if this item requires cut-to-size dimensions"""
        if 'cut to size' in (self.name or '').lower():
            return True
        if 'cut to size' in (self.description or '').lower():
            return True
        if self.stock_item and 'cut to size' in (self.stock_item.description or '').lower():
            return True
        return False

    @property
    def cut_size_display(self):
        """Formatted cut-to-size dimensions"""
        if self.cut_width and self.cut_height:
            return f"{self.cut_width:.0f} x {self.cut_height:.0f}mm"
        return ''

    @property
    def available_quantity(self):
        """Get available quantity from linked stock item"""
        if self.stock_item:
            return self.stock_item.quantity
        return 0

    @property
    def allocated_quantity(self):
        """Get quantity allocated to other non-completed jobs (excluding already-allocated items)"""
        from django.db.models import Sum
        # Get all accessories with same SKU, excluding current order, completed jobs,
        # and items that have already been allocated (stock already deducted)
        allocated = Accessory.objects.filter(
            sku=self.sku,
            order__job_finished=False,
            is_allocated=False
        ).exclude(
            order=self.order
        ).aggregate(total=Sum('quantity'))['total'] or 0
        return allocated

    @property
    def incoming_quantity(self):
        """Get quantity on order (Approved POs not yet received) for this SKU"""
        from django.db.models import Sum
        incoming = PurchaseOrderProduct.objects.filter(
            sku=self.sku,
            purchase_order__status='Approved'
        ).aggregate(
            total=Sum('order_quantity')
        )['total'] or 0
        return incoming

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
    description = models.TextField(blank=True, default='')
    cost = models.DecimalField(max_digits=10, decimal_places=2)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    stock_take_group = models.ForeignKey(StockTakeGroup, on_delete=models.SET_NULL, 
                                       null=True, blank=True, related_name='stock_items')
    supplier = models.ForeignKey('Supplier', on_delete=models.SET_NULL, null=True, blank=True, related_name='stock_items')
    supplier_code = models.CharField(max_length=100, blank=True, default='', help_text="Supplier's own product/part code")
    supplier_sku = models.CharField(max_length=100, blank=True, default='', help_text="Supplier's SKU for this product")
    category_name = models.CharField(max_length=100, blank=True)  # For CSV compatibility
    location = models.CharField(max_length=100)
    quantity = models.IntegerField(db_index=True)
    serial_or_batch = models.CharField(max_length=100, blank=True, null=True)
    last_checked = models.DateTimeField(null=True, blank=True)
    tracking_type = models.CharField(max_length=30, choices=TRACKING_CHOICES, default='not-classified', db_index=True)
    min_order_qty = models.IntegerField(blank=True, null=True)
    par_level = models.IntegerField(default=0, help_text='Minimum stock level - alerts when stock falls below this')
    image = models.ImageField(upload_to='product_images/', blank=True, null=True)
    
    # Product dimensions
    length = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text='Length in mm')
    width = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text='Width in mm')
    height = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text='Height in mm')
    weight = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text='Weight in kg')
    
    # Box / packaging dimensions for storage planning
    box_length = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text='Box length in mm')
    box_width = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text='Box width in mm')
    box_height = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text='Box height in mm')
    box_quantity = models.IntegerField(null=True, blank=True, help_text='Number of items per box')
    
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
    fax = models.CharField(max_length=100, blank=True, null=True)
    website = models.CharField(max_length=255, blank=True, null=True)
    
    # Address
    address_1 = models.CharField(max_length=255, blank=True, null=True)
    address_2 = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    state = models.CharField(max_length=100, blank=True, null=True)
    postcode = models.CharField(max_length=20, blank=True, null=True)
    country = models.CharField(max_length=100, blank=True, null=True)
    
    # Financial
    currency = models.CharField(max_length=10, blank=True, null=True)
    abn = models.CharField(max_length=50, blank=True, null=True, help_text='Tax / ABN / VAT number')
    credit_limit = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    credit_days = models.CharField(max_length=20, blank=True, null=True)
    number_of_credit_days = models.IntegerField(null=True, blank=True)
    credit_terms_type = models.CharField(max_length=50, blank=True, null=True)
    price_tier = models.CharField(max_length=100, blank=True, null=True)
    supplier_tax_rate = models.CharField(max_length=100, blank=True, null=True)
    estimate_lead_time = models.IntegerField(null=True, blank=True, help_text='Estimated lead time in days')
    
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


class SupplierContact(models.Model):
    """Individual contacts for a supplier (e.g. sales reps, account managers)"""
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='contacts')
    first_name = models.CharField(max_length=100, blank=True, default='')
    last_name = models.CharField(max_length=100, blank=True, default='')
    email = models.EmailField(max_length=255, blank=True, default='')
    phone = models.CharField(max_length=100, blank=True, default='')
    position = models.CharField(max_length=150, blank=True, default='')
    is_default = models.BooleanField(default=False, help_text='Use this contact as the default email recipient for POs')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-is_default', 'last_name', 'first_name']

    def __str__(self):
        return f"{self.first_name} {self.last_name} <{self.email}>"

    def save(self, *args, **kwargs):
        # Ensure only one default per supplier
        if self.is_default:
            SupplierContact.objects.filter(supplier=self.supplier, is_default=True).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)


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
    issue_date = models.CharField(max_length=50, blank=True, null=True)
    expected_date = models.CharField(max_length=50, blank=True, null=True)
    received_date = models.CharField(max_length=50, blank=True, null=True)
    invoice_date = models.CharField(max_length=50, blank=True, null=True)
    
    # Status and Financials
    status = models.CharField(max_length=50, default='Draft', blank=True, null=True)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    forecast_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    base_currency_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    currency = models.CharField(max_length=10, default='GBP', blank=True, null=True)
    exchange_rate = models.DecimalField(max_digits=10, decimal_places=4, default=1.0)
    
    # Financials - extended
    tax_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    base_currency_tax_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    invoiced_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    amount_outstanding = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    volume = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    weight = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    cis_deduction = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    # Delivery
    warehouse_id = models.IntegerField(null=True, blank=True)
    warehouse_name = models.CharField(max_length=200, blank=True, null=True)
    delivery_address_1 = models.CharField(max_length=255, blank=True, null=True)
    delivery_address_2 = models.CharField(max_length=255, blank=True, null=True)
    delivery_instructions = models.TextField(blank=True, null=True)
    suburb = models.CharField(max_length=100, blank=True, null=True)
    state = models.CharField(max_length=100, blank=True, null=True)
    postcode = models.CharField(max_length=20, blank=True, null=True)
    
    # Dates - extended
    approved_date = models.CharField(max_length=50, blank=True, null=True)
    invoice_due_date = models.CharField(max_length=50, blank=True, null=True)
    
    # Client/Contact
    client_id_wg = models.IntegerField(null=True, blank=True, help_text='WorkGuru Client ID')
    client_name = models.CharField(max_length=200, blank=True, null=True)
    contact_name = models.CharField(max_length=200, blank=True, null=True)
    
    # Accounting
    accounting_system_number = models.CharField(max_length=100, blank=True, null=True)
    
    # Flags
    sent_to_supplier = models.CharField(max_length=50, blank=True, null=True)
    sent_to_accounting = models.CharField(max_length=50, blank=True, null=True)
    billable = models.BooleanField(default=False)
    email_sent = models.BooleanField(default=False, help_text='Whether PO email has been sent to supplier')
    email_sent_at = models.DateTimeField(null=True, blank=True, help_text='When the PO email was sent')
    email_sent_to = models.CharField(max_length=255, blank=True, null=True, help_text='Email address PO was sent to')
    is_advanced = models.BooleanField(default=False)
    is_rfq = models.BooleanField(default=False)
    is_landed_costs_po = models.BooleanField(default=False)
    stock_used_on_projects = models.BooleanField(default=False)
    
    # Metadata
    creator_name = models.CharField(max_length=200, blank=True, null=True)
    received_by_name = models.CharField(max_length=200, blank=True, null=True)
    approved_by_name = models.CharField(max_length=200, blank=True, null=True)
    creation_time_wg = models.CharField(max_length=50, blank=True, null=True, help_text='WorkGuru creation timestamp')
    last_modification_time_wg = models.CharField(max_length=50, blank=True, null=True, help_text='WorkGuru last modification timestamp')
    
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
    workguru_id = models.IntegerField(null=True, blank=True, help_text='WorkGuru product line ID')
    product_id = models.IntegerField(null=True, blank=True, help_text='WorkGuru Product ID')
    sku = models.CharField(max_length=100, blank=True)
    supplier_code = models.CharField(max_length=100, blank=True)
    name = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    notes = models.TextField(blank=True, null=True)
    
    # Pricing
    order_price = models.DecimalField(max_digits=10, decimal_places=5, default=0)
    order_quantity = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    quantity = models.DecimalField(max_digits=10, decimal_places=4, default=0, help_text='Quantity field from API')
    received_quantity = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    invoice_price = models.DecimalField(max_digits=10, decimal_places=5, default=0)
    line_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    unit_cost = models.DecimalField(max_digits=10, decimal_places=5, default=0)
    minimum_order_quantity = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    
    # Tax
    tax_type = models.CharField(max_length=50, blank=True, null=True)
    tax_name = models.CharField(max_length=100, blank=True, null=True)
    tax_rate = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    # Accounting
    account_code = models.CharField(max_length=50, blank=True, null=True)
    expense_account_code = models.CharField(max_length=50, blank=True, null=True)
    
    # Ordering
    sort_order = models.IntegerField(default=0)
    weight = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    received_date = models.CharField(max_length=50, blank=True, null=True)
    
    # Link to local stock item if available
    stock_item = models.ForeignKey(StockItem, on_delete=models.SET_NULL, null=True, blank=True, related_name='purchase_order_lines')
    
    class Meta:
        ordering = ['sort_order', 'id']
    
    def __str__(self):
        return f"{self.purchase_order.display_number} - {self.sku} - {self.name}"


class PurchaseOrderAttachment(models.Model):
    """File attachments on a purchase order (PNX, CSV, PDF, etc.)"""
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField(upload_to='po_attachments/')
    filename = models.CharField(max_length=255)
    description = models.CharField(max_length=255, blank=True)
    uploaded_by = models.CharField(max_length=200, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return f"{self.purchase_order.display_number} - {self.filename}"


class PurchaseOrderInvoice(models.Model):
    """Supplier invoice attached to a purchase order."""

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('paid', 'Paid'),
    ]

    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='invoices')
    invoice_number = models.CharField(max_length=100, blank=True)
    file = models.FileField(upload_to='po_invoices/', blank=True, null=True)
    filename = models.CharField(max_length=255, blank=True)
    date = models.DateField(null=True, blank=True, help_text='Invoice date')
    due_date = models.DateField(null=True, blank=True, help_text='Payment due date')
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default='GBP')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    notes = models.TextField(blank=True)
    uploaded_by = models.CharField(max_length=200, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-uploaded_at']

    def __str__(self):
        return f"{self.purchase_order.display_number} – Invoice {self.invoice_number or '(no number)'}"


class PurchaseOrderProject(models.Model):
    """Associates a PurchaseOrder with one or more projects/orders.

    A PO can be for 'Stock' and/or for specific customer orders.  Each entry
    carries the label displayed in the Project section (e.g. "Stock",
    "S12345 - Smith", etc.) and an optional link to the local Order record.
    """
    TYPE_CHOICES = [
        ('stock', 'Stock'),
        ('customer', 'Customer'),
    ]

    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='projects')
    project_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default='customer')
    order = models.ForeignKey(Order, on_delete=models.SET_NULL, null=True, blank=True, related_name='po_projects',
                              help_text='Linked local order (null for stock entries)')
    label = models.CharField(max_length=255, blank=True, help_text='Display label, e.g. "Stock" or customer name')
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sort_order', 'created_at']

    def __str__(self):
        return f"{self.purchase_order.display_number} → {self.label or self.project_type}"


class ProductCustomerAllocation(models.Model):
    """Links a quantity of a PO product line to a specific order/customer."""
    product = models.ForeignKey(PurchaseOrderProduct, on_delete=models.CASCADE, related_name='allocations')
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='po_allocations')
    quantity = models.DecimalField(max_digits=10, decimal_places=4, default=1)
    notes = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.product} -> {self.order.sale_number} (x{self.quantity})"


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


class SalesAppointment(models.Model):
    """Track sales team appointments for the sales calendar."""
    EVENT_TYPE_CHOICES = [
        ('appointment', 'Sales Appointment'),
        ('showroom_cover', 'Showroom Cover'),
        ('unavailable', 'Unavailable'),
    ]
    event_type = models.CharField(max_length=20, choices=EVENT_TYPE_CHOICES, default='appointment', help_text='Type of event')
    designer = models.CharField(max_length=100, blank=True, help_text='Designer / salesperson name')
    customer_name = models.CharField(max_length=200, blank=True, help_text='Customer full name')
    postcode = models.CharField(max_length=20, blank=True, help_text='Customer postcode')
    appointment_date = models.DateField(help_text='Date of the appointment')
    appointment_time = models.TimeField(help_text='Start time of the appointment')
    end_time = models.TimeField(null=True, blank=True, help_text='End time of the appointment')
    notes = models.TextField(blank=True, help_text='Additional notes')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['appointment_date', 'appointment_time']

    def __str__(self):
        label = self.get_event_type_display()
        name = self.customer_name or self.designer or label
        return f"{label} – {name} @ {self.appointment_date} {self.appointment_time:%H:%M}"


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
    display_order = models.IntegerField(default=0, help_text='Order workers are displayed (lower = first)')
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return self.name
    
    class Meta:
        ordering = ['display_order', 'name']


class Timesheet(models.Model):
    """Timesheet entries for both fitters and factory workers"""
    TIMESHEET_TYPE_CHOICES = [
        ('installation', 'Installation'),
        ('manufacturing', 'Manufacturing'),
    ]
    
    order = models.ForeignKey(Order, on_delete=models.SET_NULL, null=True, blank=True, related_name='timesheets')
    timesheet_type = models.CharField(max_length=20, choices=TIMESHEET_TYPE_CHOICES)
    
    # Worker - use either fitter or factory_worker
    fitter = models.ForeignKey(Fitter, on_delete=models.SET_NULL, null=True, blank=True, related_name='timesheets')
    factory_worker = models.ForeignKey(FactoryWorker, on_delete=models.SET_NULL, null=True, blank=True, related_name='timesheets')
    
    date = models.DateField()
    
    # For installation timesheets (linked PO)
    purchase_order = models.ForeignKey('PurchaseOrder', on_delete=models.SET_NULL, null=True, blank=True, related_name='timesheets', help_text='Associated PO for installation cost')
    
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
        return 'Unknown'
    
    @property
    def worker_type(self):
        """Get the worker type"""
        if self.fitter:
            return 'fitter'
        elif self.factory_worker:
            return 'factory_worker'
        return 'unknown'
    
    @property
    def total_cost(self):
        """Calculate total cost for this timesheet entry"""
        if self.timesheet_type == 'installation' and self.purchase_order:
            # Installation uses linked PO total
            return self.purchase_order.total
        elif self.hours and self.hourly_rate:
            # Manufacturing uses hours × hourly_rate
            return self.hours * self.hourly_rate
        return 0
    
    def __str__(self):
        if self.timesheet_type == 'installation':
            po_ref = self.purchase_order.display_number if self.purchase_order else 'no PO'
            return f"{self.worker_name} - {self.date} ({po_ref})"
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


# =============================================
# Invoices (synced from WorkGuru)
# =============================================

class Invoice(models.Model):
    """Invoice synced from WorkGuru."""

    STATUS_CHOICES = [
        ('Draft', 'Draft'),
        ('Approved', 'Approved'),
        ('Sent', 'Sent'),
        ('Paid', 'Paid'),
        ('Void', 'Void'),
    ]

    PAYMENT_STATUS_CHOICES = [
        ('paid', 'Paid'),
        ('partial', 'Partial'),
        ('unpaid', 'Unpaid'),
    ]

    # WorkGuru identifiers
    workguru_id = models.IntegerField(unique=True, null=True, blank=True, help_text='WorkGuru Invoice ID')
    invoice_number = models.CharField(max_length=50, db_index=True)

    # Client
    client_name = models.CharField(max_length=255, blank=True)
    client_id = models.IntegerField(null=True, blank=True, help_text='WorkGuru Client ID')
    customer = models.ForeignKey(
        'Customer', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='invoices',
        help_text='Link to local Customer record',
    )

    # Project
    project_name = models.CharField(max_length=255, blank=True)
    project_number = models.CharField(max_length=100, blank=True)
    project_id = models.IntegerField(null=True, blank=True, help_text='WorkGuru Project ID')
    order = models.ForeignKey(
        'Order', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='invoices',
        help_text='Link to local Order record',
    )

    # Dates
    date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    sent_to_accounting = models.DateField(null=True, blank=True)

    # Details
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Draft')
    description = models.TextField(blank=True)
    invoice_reference = models.CharField(max_length=255, blank=True)
    client_po = models.CharField(max_length=100, blank=True, help_text='Client PO number')

    # Financial
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_tax = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    freight_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text='Freight / shipping cost')
    amount_outstanding = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_status = models.CharField(max_length=10, choices=PAYMENT_STATUS_CHOICES, default='unpaid')
    is_overdue = models.BooleanField(default=False)
    currency = models.CharField(max_length=3, default='GBP', help_text='ISO currency code')
    is_vat_inclusive = models.BooleanField(default=True, help_text='Whether the total entered includes VAT')
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=20.00, help_text='VAT percentage rate')

    # Xero / accounting integration
    xero_id = models.CharField(max_length=100, blank=True, null=True)

    # Linked purchase orders (M2M – an invoice can be attached to multiple POs)
    purchase_orders = models.ManyToManyField(
        'PurchaseOrder', blank=True, related_name='linked_invoices',
        help_text='Purchase orders this invoice is attached to',
    )

    # Linked PO products (partial linking – specific line items from POs)
    linked_products = models.ManyToManyField(
        'PurchaseOrderProduct', blank=True, related_name='linked_invoices',
        help_text='Specific PO line items this invoice covers',
    )

    # PDF attachment
    attachment = models.FileField(
        upload_to='invoice_attachments/', blank=True, null=True,
        help_text='PDF or document attached to this invoice',
    )

    # Metadata
    raw_data = models.JSONField(null=True, blank=True, help_text='Full API response')
    synced_at = models.DateTimeField(null=True, blank=True, help_text='Last sync timestamp')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.invoice_number} – {self.client_name}"

    class Meta:
        ordering = ['-date', '-created_at']


class InvoiceLineItem(models.Model):
    """Line item on a WorkGuru invoice."""
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='line_items')
    workguru_id = models.IntegerField(null=True, blank=True)

    name = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    rate = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    quantity = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    tax_name = models.CharField(max_length=100, blank=True)
    tax_rate = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    line_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'id']

    def __str__(self):
        return f"{self.name} ({self.invoice.invoice_number})"


class InvoicePayment(models.Model):
    """Payment recorded against a WorkGuru invoice."""
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='payments')
    workguru_id = models.IntegerField(null=True, blank=True)

    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    name = models.CharField(max_length=255, blank=True, help_text='Payment name / note')
    date = models.DateTimeField(null=True, blank=True)
    sent_to_accounting = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return f"£{self.amount} – {self.name} ({self.invoice.invoice_number})"


# =============================================
# Role-Based Access Control
# =============================================

# All page codenames organised by nav section
PAGE_SECTIONS = [
    ('Dashboard', [
        ('dashboard', 'Dashboard'),
    ]),
    ('Projects', [
        ('orders', 'Orders'),
        ('order_details', 'Order Details'),
        ('customers', 'Customers'),
        ('customer_details', 'Customer Details'),
        ('remedials', 'Remedials'),
    ]),
    ('Accounting', [
        ('invoices', 'Invoices'),
    ]),
    ('Purchase', [
        ('purchase_orders', 'Purchase Orders'),
        ('purchase_order_details', 'Purchase Order Details'),
        ('suppliers', 'Suppliers'),
        ('supplier_details', 'Supplier Details'),
        ('boards_summary', 'Boards Summary'),
        ('os_doors_summary', 'OS Doors Summary'),
        ('material_shortage', 'Stock Shortage Report'),
        ('raumplus_storage', 'Raumplus Shortage'),
    ]),
    ('Products & Stock', [
        ('products', 'Products'),
        ('product_details', 'Product Details'),
        ('stock_list', 'Stock List'),
        ('stock_take', 'Stock Take'),
        ('categories', 'Categories'),
        ('substitutions', 'Substitutions'),
    ]),
    ('Calendar', [
        ('fit_board', 'Fit Board'),
        ('timesheets', 'Timesheets'),
        ('workflow', 'Workflow'),
    ]),
    ('Tools', [
        ('map', 'Map'),
        ('generate_materials', 'Order Generator'),
        ('database_check', 'Database Check'),
    ]),
    ('Reports', [
        ('material_report', 'Material Report'),
        ('costing_report', 'Costing Report'),
        ('remedial_report', 'Remedial Report'),
    ]),
    ('Other', [
        ('tickets', 'Tickets'),
        ('claim_service', 'Claim Service'),
        ('admin_panel', 'Admin Panel'),
    ]),
]

# Flat list of all page choices
PAGE_CHOICES = []
for section_name, pages in PAGE_SECTIONS:
    for codename, label in pages:
        PAGE_CHOICES.append((codename, label))


class Role(models.Model):
    """User role defining access permissions across the application."""
    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('accounting', 'Accounting'),
        ('director', 'Director'),
        ('user', 'User'),
        ('franchise', 'Franchise'),
    ]

    name = models.CharField(max_length=20, choices=ROLE_CHOICES, unique=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.get_name_display()

    def is_admin(self):
        return self.name == 'admin'

    def has_page_permission(self, page_codename, action='view'):
        """Check if this role has a specific permission on a page."""
        if self.name == 'admin':
            return True
        try:
            perm = self.page_permissions.get(page_codename=page_codename)
            return getattr(perm, f'can_{action}', False)
        except PagePermission.DoesNotExist:
            return False

    def get_accessible_pages(self):
        """Return set of page codenames this role can view."""
        if self.name == 'admin':
            return {codename for codename, _ in PAGE_CHOICES}
        return set(
            self.page_permissions
                .filter(can_view=True)
                .values_list('page_codename', flat=True)
        )

    class Meta:
        ordering = ['name']


class PagePermission(models.Model):
    """Defines CRUD permissions for a specific page within a role."""
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name='page_permissions')
    page_codename = models.CharField(max_length=50, choices=PAGE_CHOICES)
    can_view = models.BooleanField(default=False)
    can_create = models.BooleanField(default=False)
    can_edit = models.BooleanField(default=False)
    can_delete = models.BooleanField(default=False)

    def __str__(self):
        perms = []
        if self.can_view: perms.append('View')
        if self.can_create: perms.append('Create')
        if self.can_edit: perms.append('Edit')
        if self.can_delete: perms.append('Delete')
        return f"{self.role} - {self.get_page_codename_display()} [{', '.join(perms) or 'None'}]"

    class Meta:
        unique_together = ('role', 'page_codename')
        ordering = ['role', 'page_codename']


class UserProfile(models.Model):
    """User profile to store user preferences and role assignment"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    dark_mode = models.BooleanField(default=True, help_text='Enable dark mode theme')
    selected_location = models.CharField(max_length=100, blank=True, default='', help_text='Currently selected site location')
    role = models.ForeignKey(Role, on_delete=models.SET_NULL, null=True, blank=True, related_name='users')

    def __str__(self):
        role_display = self.role.get_name_display() if self.role else 'No Role'
        return f"{self.user.username}'s profile ({role_display})"

    def has_page_permission(self, page_codename, action='view'):
        """Check if the user has a specific permission on a page."""
        if self.user.is_superuser:
            return True
        if not self.role:
            return False
        return self.role.has_page_permission(page_codename, action)

    def get_accessible_pages(self):
        """Return set of page codenames this user can view."""
        if self.user.is_superuser:
            return {codename for codename, _ in PAGE_CHOICES}
        if not self.role:
            return set()
        return self.role.get_accessible_pages()

    def can_view(self, page_codename):
        return self.has_page_permission(page_codename, 'view')

    def can_create(self, page_codename):
        return self.has_page_permission(page_codename, 'create')

    def can_edit(self, page_codename):
        return self.has_page_permission(page_codename, 'edit')

    def can_delete(self, page_codename):
        return self.has_page_permission(page_codename, 'delete')


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """Create a UserProfile when a new User is created, auto-assigning Franchise role."""
    if created:
        franchise_role = Role.objects.filter(name='franchise').first()
        UserProfile.objects.create(user=instance, role=franchise_role)


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    """Save the UserProfile when the User is saved"""
    if hasattr(instance, 'profile'):
        instance.profile.save()


class Ticket(models.Model):
    """Support ticket for reporting issues"""
    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
    ]
    
    STATUS_CHOICES = [
        ('open', 'Open'),
        ('in_progress', 'In Progress'),
        ('resolved', 'Resolved'),
        ('closed', 'Closed'),
    ]
    
    title = models.CharField(max_length=200)
    description = models.TextField()
    image = models.ImageField(upload_to='ticket_images/', blank=True, null=True)
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='medium')
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='open')
    submitted_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='tickets')
    read_by_admin = models.BooleanField(default=False, help_text='Has an admin read this ticket?')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"#{self.id} - {self.title}"


class ClaimDocument(models.Model):
    """PDF document for the Claim Service."""
    title = models.CharField(max_length=255, help_text='Display title for the claim')
    file = models.FileField(upload_to='claim_documents/')
    customer_name = models.CharField(max_length=255, blank=True, help_text='Customer name for search')
    group_key = models.CharField(max_length=255, blank=True, db_index=True,
                                 help_text='Groups related PDFs together, e.g. 1111_Radley_022115')
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='uploaded_claims')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    # Download tracking
    downloaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='downloaded_claims')
    downloaded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return self.title

    @property
    def filename(self):
        import os
        return os.path.basename(self.file.name) if self.file else ''

    @property
    def doc_type(self):
        """Extract document type from filename (e.g. 'ProductionDrawings')."""
        import os
        name = os.path.splitext(os.path.basename(self.file.name))[0] if self.file else ''
        parts = name.rsplit('_', 1)
        return parts[-1] if len(parts) > 1 else name

    @staticmethod
    def extract_group_key(filename):
        """Extract group key from a filename like '1111_Radley_022115_ProductionDrawings.PDF'."""
        import os
        name = os.path.splitext(filename)[0]
        parts = name.rsplit('_', 1)
        return parts[0] if len(parts) > 1 else ''

    @staticmethod
    def extract_customer_name(filename):
        """Extract customer name from filename pattern '{number}_{name}_{id}_{type}.PDF'."""
        import os
        name = os.path.splitext(filename)[0]
        parts = name.split('_')
        if len(parts) >= 3:
            return parts[1]
        return ''


class XeroToken(models.Model):
    """
    Stores Xero OAuth2 tokens. Only one active token set at a time.
    Access tokens expire every 30 minutes; refresh tokens last 60 days.
    """
    access_token = models.TextField()
    refresh_token = models.TextField()
    token_type = models.CharField(max_length=50, default='Bearer')
    expires_at = models.DateTimeField(help_text='When the access token expires')
    scope = models.TextField(blank=True, default='')
    tenant_id = models.CharField(max_length=100, blank=True, default='', help_text='Xero Tenant (Organisation) ID')
    tenant_name = models.CharField(max_length=255, blank=True, default='', help_text='Xero Organisation name')
    connected_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f"Xero Token ({self.tenant_name or 'no tenant'}) - updated {self.updated_at}"

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @classmethod
    def get_active_token(cls):
        """Return the most recently updated token, or None."""
        return cls.objects.first()


class SyncLog(models.Model):
    """Log entry for API sync scripts (Anthill, Xero, WorkGuru, etc.)"""

    STATUS_CHOICES = [
        ('success', 'Success'),
        ('warning', 'Warning'),
        ('error', 'Error'),
    ]

    script_name = models.CharField(max_length=100, db_index=True, help_text='Name/identifier of the sync script')
    ran_at = models.DateTimeField(default=timezone.now, help_text='When this run started')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='success')
    records_created = models.IntegerField(default=0, help_text='Number of records created this run')
    records_updated = models.IntegerField(default=0, help_text='Number of records updated this run')
    errors = models.IntegerField(default=0, help_text='Number of errors encountered')
    notes = models.TextField(blank=True, help_text='Free-text summary or error details')

    class Meta:
        ordering = ['-ran_at']
        indexes = [
            models.Index(fields=['script_name', '-ran_at']),
        ]

    def __str__(self):
        return f"{self.script_name} @ {self.ran_at.strftime('%Y-%m-%d %H:%M')} [{self.get_status_display()}]"
