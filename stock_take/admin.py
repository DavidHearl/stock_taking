from django.contrib import admin
from django.db.models import F, ExpressionWrapper, DecimalField
from .models import BoardsPO, Order, OSDoor, StockItem, Category, StockTakeGroup, ImportHistory

@admin.register(BoardsPO)
class BoardsPOAdmin(admin.ModelAdmin):
    list_display = ['po_number', 'boards_ordered', 'file']
    search_fields = ['po_number']
    list_filter = ['boards_ordered']

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['sale_number', 'first_name', 'last_name', 'customer_number', 'order_date', 'fit_date', 'boards_po']
    search_fields = ['sale_number', 'first_name', 'last_name', 'customer_number']
    list_filter = ['order_date', 'boards_po']

@admin.register(OSDoor)
class OSDoorAdmin(admin.ModelAdmin):
    list_display = ['customer', 'door_style', 'style_colour', 'height', 'width', 'colour', 'quantity', 'ordered', 'received']
    search_fields = ['customer__sale_number', 'door_style', 'style_colour']
    list_filter = ['ordered', 'received', 'door_style', 'colour']

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'color', 'item_count']
    search_fields = ['name']
    
    def item_count(self, obj):
        return obj.stockitem_set.count()
    item_count.short_description = 'Items'

@admin.register(StockTakeGroup)
class StockTakeGroupAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'item_count']
    search_fields = ['name']
    list_filter = ['category']
    
    def item_count(self, obj):
        return obj.stockitem_set.count()
    item_count.short_description = 'Items'

@admin.register(ImportHistory)
class ImportHistoryAdmin(admin.ModelAdmin):
    list_display = ['filename', 'imported_at', 'record_count']
    search_fields = ['filename']
    list_filter = ['imported_at']
    readonly_fields = ['filename', 'imported_at', 'record_count']
    
    def has_add_permission(self, request):
        return False

@admin.register(StockItem)
class StockItemAdmin(admin.ModelAdmin):
    list_display = ['sku', 'name', 'quantity', 'tracking_type', 'cost', 'total_value', 'location', 'category']
    search_fields = ['sku', 'name', 'location', 'serial_or_batch']
    list_filter = ['tracking_type', 'category', 'stock_take_group']
    list_editable = ['tracking_type', 'quantity']
    ordering = ['sku']
    list_per_page = 50
    
    fieldsets = (
        ('Product Information', {
            'fields': ('sku', 'name', 'cost', 'quantity', 'category', 'stock_take_group')
        }),
        ('Classification', {
            'fields': ('tracking_type',)
        }),
        ('Additional Details', {
            'fields': ('location', 'serial_or_batch'),
            'classes': ('collapse',)
        }),
    )
    
    def total_value(self, obj):
        return f"£{(obj.cost * obj.quantity):.2f}"
    total_value.short_description = 'Total Value'
    
    def get_queryset(self, request):
        # Use select_related for better performance
        qs = super().get_queryset(request)
        return qs.select_related('category', 'stock_take_group')
    
    actions = ['mark_as_stock', 'mark_as_non_stock', 'mark_as_not_classified']
    
    @admin.action(description='Mark as Stock')
    def mark_as_stock(self, request, queryset):
        updated = queryset.update(tracking_type='stock')
        self.message_user(request, f'{updated} items marked as Stock')
    
    @admin.action(description='Mark as Non-Stock')
    def mark_as_non_stock(self, request, queryset):
        updated = queryset.update(tracking_type='non-stock')
        self.message_user(request, f'{updated} items marked as Non-Stock')
    
    @admin.action(description='Mark as Not Classified')
    def mark_as_not_classified(self, request, queryset):
        updated = queryset.update(tracking_type='not-classified')
        self.message_user(request, f'{updated} items marked as Not Classified')
