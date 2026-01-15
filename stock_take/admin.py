from django.contrib import admin
from django.db.models import F, ExpressionWrapper, DecimalField
from .models import (
    BoardsPO, Order, OSDoor, StockItem, Category, StockTakeGroup, ImportHistory, 
    Remedial, RemedialAccessory, FitAppointment, WorkflowStage, WorkflowTask, 
    OrderWorkflowProgress, TaskCompletion
)

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

@admin.register(Remedial)
class RemedialAdmin(admin.ModelAdmin):
    list_display = ['remedial_number', 'original_order', 'customer_name', 'reason', 'created_date', 'scheduled_date', 'is_completed']
    search_fields = ['remedial_number', 'original_order__sale_number', 'first_name', 'last_name', 'customer_number']
    list_filter = ['is_completed', 'created_date', 'scheduled_date', 'boards_po']
    readonly_fields = ['created_date', 'days_since_created']
    
    def customer_name(self, obj):
        return f"{obj.first_name} {obj.last_name}"
    customer_name.short_description = 'Customer'

@admin.register(RemedialAccessory)
class RemedialAccessoryAdmin(admin.ModelAdmin):
    list_display = ['remedial', 'sku', 'name', 'quantity', 'ordered', 'received']
    search_fields = ['remedial__remedial_number', 'sku', 'name']
    list_filter = ['ordered', 'received']


@admin.register(FitAppointment)
class FitAppointmentAdmin(admin.ModelAdmin):
    list_display = ['customer_name', 'fit_date', 'fitter', 'interior_completed', 'door_completed', 'accessories_completed', 'materials_completed', 'is_fully_completed']
    search_fields = ['order__sale_number', 'order__first_name', 'order__last_name']
    list_filter = ['fit_date', 'fitter', 'interior_completed', 'door_completed', 'accessories_completed', 'materials_completed']
    readonly_fields = ['customer_name', 'is_fully_completed', 'created_at', 'updated_at']
    
    def customer_name(self, obj):
        return obj.customer_name
    customer_name.short_description = 'Customer'


class WorkflowTaskInline(admin.TabularInline):
    model = WorkflowTask
    extra = 1
    fields = ['description', 'order']


@admin.register(WorkflowStage)
class WorkflowStageAdmin(admin.ModelAdmin):
    list_display = ['name', 'phase', 'role', 'expected_days', 'order']
    list_filter = ['phase', 'role']
    search_fields = ['name', 'description']
    inlines = [WorkflowTaskInline]
    list_editable = ['order']


@admin.register(WorkflowTask)
class WorkflowTaskAdmin(admin.ModelAdmin):
    list_display = ['description', 'stage', 'order']
    list_filter = ['stage']
    search_fields = ['description']


@admin.register(OrderWorkflowProgress)
class OrderWorkflowProgressAdmin(admin.ModelAdmin):
    list_display = ['order', 'current_stage', 'stage_started_at', 'stage_updated_at']
    list_filter = ['current_stage', 'stage_started_at']
    search_fields = ['order__sale_number', 'order__first_name', 'order__last_name']
    readonly_fields = ['stage_started_at', 'stage_updated_at']


@admin.register(TaskCompletion)
class TaskCompletionAdmin(admin.ModelAdmin):
    list_display = ['order_progress', 'task', 'completed', 'completed_at', 'completed_by']
    list_filter = ['completed', 'completed_at', 'task__stage']
    search_fields = ['order_progress__order__sale_number', 'task__description']
    readonly_fields = ['completed_at']
