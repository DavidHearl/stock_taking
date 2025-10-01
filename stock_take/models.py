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


class StockItem(models.Model):
    sku = models.CharField(max_length=100)
    name = models.CharField(max_length=200)
    cost = models.DecimalField(max_digits=10, decimal_places=2)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    category_name = models.CharField(max_length=100, blank=True)  # For CSV compatibility
    location = models.CharField(max_length=100)
    quantity = models.IntegerField()
    serial_or_batch = models.CharField(max_length=100, blank=True, null=True)
    
    @property
    def total_value(self):
        return self.cost * self.quantity
    
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
    categories = models.ManyToManyField(Category, blank=True)
    locations = models.TextField(help_text='Comma-separated list of locations')
    scheduled_date = models.DateTimeField()
    created_date = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    assigned_to = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    
    class Meta:
        ordering = ['scheduled_date']
    
    def __str__(self):
        return f"{self.name} - {self.scheduled_date.strftime('%Y-%m-%d')}"
    
    @property
    def is_overdue(self):
        return self.scheduled_date < timezone.now() and self.status != 'completed'

class ImportHistory(models.Model):
    imported_at = models.DateTimeField(default=timezone.now)
    filename = models.CharField(max_length=255)
    record_count = models.IntegerField()
    
    class Meta:
        ordering = ['-imported_at']
    
    def __str__(self):
        return f"Import on {self.imported_at} - {self.record_count} records"