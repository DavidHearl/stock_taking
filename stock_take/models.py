from django.db import models
from django.utils import timezone
from decimal import Decimal

class BoardsPO(models.Model):
    po_number = models.CharField(max_length=50, unique=True)
    file = models.FileField(upload_to='boards_po_files/', blank=True, null=True)
    boards_ordered = models.BooleanField(default=False)

    def __str__(self):
        return self.po_number

    @property
    def boards_received(self):
        """Check if all PNX items have been received"""
        if not self.pnx_items.exists():
            return False
        return self.pnx_items.filter(received=False).count() == 0


class Order(models.Model):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    sale_number = models.CharField(max_length=6)
    customer_number = models.CharField(max_length=6)
    order_date = models.DateField()
    fit_date = models.DateField()
    boards_po = models.ForeignKey(BoardsPO, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    job_finished = models.BooleanField(default=False)
    address = models.CharField(max_length=255, blank=True)
    postcode = models.CharField(max_length=20, blank=True)
    ORDER_TYPE_CHOICES = [
        ('sale', 'Sale'),
        ('remedial', 'Remedial'),
        ('warranty', 'Warranty'),
    ]
    order_type = models.CharField(max_length=20, choices=ORDER_TYPE_CHOICES, default='sale')
    os_doors_required = models.BooleanField(default=False, help_text='True if OS Doors are required for this order')
    os_doors_po = models.CharField(max_length=50, blank=True, help_text='PO number when OS Doors are ordered')
    all_items_ordered = models.BooleanField(default=False, help_text='Manual confirmation that all items have been ordered')
    anthill_id = models.CharField(max_length=20, blank=True, help_text='Anthill CRM Customer ID')
    workguru_id = models.CharField(max_length=20, blank=True, help_text='WorkGuru Project ID')
    original_csv = models.FileField(upload_to='order_csvs/', blank=True, null=True, help_text='Original uploaded CSV file')
    processed_csv = models.FileField(upload_to='order_csvs/', blank=True, null=True, help_text='Processed CSV with substitutions applied')
    original_csv_uploaded_at = models.DateTimeField(blank=True, null=True, help_text='When the original CSV was uploaded')
    processed_csv_created_at = models.DateTimeField(blank=True, null=True, help_text='When the processed CSV was created')
    csv_has_missing_items = models.BooleanField(default=False, help_text='True if the uploaded CSV has unresolved missing items that need substitution')

    def time_allowance(self):
        return (self.fit_date - self.order_date).days

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
        
        # Check if all PNX items for this order are received
        return order_pnx_items.filter(received=False).count() == 0

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
        
        # Check if all OS doors for this order are received
        return self.os_doors.filter(received=False).count() == 0


class PNXItem(models.Model):
    boards_po = models.ForeignKey(BoardsPO, on_delete=models.CASCADE, related_name='pnx_items')
    barcode = models.CharField(max_length=100)
    matname = models.CharField(max_length=100)
    cleng = models.DecimalField(max_digits=10, decimal_places=2)
    cwidth = models.DecimalField(max_digits=10, decimal_places=2)
    cnt = models.DecimalField(max_digits=10, decimal_places=2)
    customer = models.CharField(max_length=200)
    received = models.BooleanField(default=False)

    # Price per square meter for boards
    PRICE_PER_SQM = 50

    def __str__(self):
        return f"{self.barcode} - {self.matname}"

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
    ordered = models.BooleanField(default=False)
    received = models.BooleanField(default=False)

    def __str__(self):
        return f"OS Door for {self.customer.sale_number} - {self.door_style}"


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
    sku = models.CharField(max_length=100)
    name = models.CharField(max_length=200)
    cost = models.DecimalField(max_digits=10, decimal_places=2)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    stock_take_group = models.ForeignKey(StockTakeGroup, on_delete=models.SET_NULL, 
                                       null=True, blank=True, related_name='stock_items')
    category_name = models.CharField(max_length=100, blank=True)  # For CSV compatibility
    location = models.CharField(max_length=100)
    quantity = models.IntegerField()
    serial_or_batch = models.CharField(max_length=100, blank=True, null=True)
    last_checked = models.DateTimeField(null=True, blank=True)
    
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