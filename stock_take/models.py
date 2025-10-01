from django.db import models
from django.utils import timezone
from decimal import Decimal


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