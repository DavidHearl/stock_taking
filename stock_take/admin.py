from django.contrib import admin
from django.db.models import F, ExpressionWrapper, DecimalField
from .models import (
    Customer, BoardsPO, Order, OSDoor, StockItem, Category, StockTakeGroup, ImportHistory, 
    Remedial, RemedialAccessory, FitAppointment, WorkflowStage, WorkflowTask, 
    OrderWorkflowProgress, TaskCompletion, Fitter, FactoryWorker, Timesheet, Expense, UserProfile,
    StockHistory, Role, PagePermission, XeroToken, SyncLog,
    AnthillSale, AnthillPayment,
    Lead, AnthillOrderToPlace, Designer, PNXItem, Accessory, Schedule, Substitution,
    CSVSkipItem, SalesAppointment, WorkflowStageDate, Supplier, SupplierContact,
    PurchaseOrder, PurchaseOrderProduct, PurchaseOrderAttachment, PurchaseOrderInvoice,
    PurchaseOrderProject, ProductCustomerAllocation, Invoice, InvoiceLineItem, InvoicePayment,
    PurchaseInvoice, PurchaseInvoiceLineItem, GalleryImage, Ticket, ClaimDocument,
    PriceHistory, ActivityLog, RaumplusDraftOrder,
)

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ['first_name', 'last_name', 'anthill_customer_id', 'postcode']
    search_fields = ['first_name', 'last_name', 'anthill_customer_id', 'address', 'postcode']
    list_filter = ['postcode']

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
    list_display = ['sku', 'name', 'quantity', 'par_level', 'tracking_type', 'cost', 'total_value', 'location', 'category']
    search_fields = ['sku', 'name', 'location', 'serial_or_batch']
    list_filter = ['tracking_type', 'category', 'stock_take_group']
    list_editable = ['tracking_type', 'quantity', 'par_level']
    ordering = ['sku']
    list_per_page = 50
    
    fieldsets = (
        ('Product Information', {
            'fields': ('sku', 'name', 'cost', 'quantity', 'category', 'stock_take_group')
        }),
        ('Stock Management', {
            'fields': ('par_level', 'min_order_qty')
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


@admin.register(StockHistory)
class StockHistoryAdmin(admin.ModelAdmin):
    list_display = ['stock_item', 'quantity', 'change_amount', 'change_type', 'reference', 'created_at', 'created_by']
    search_fields = ['stock_item__sku', 'stock_item__name', 'reference']
    list_filter = ['change_type', 'created_at']
    readonly_fields = ['created_at']
    ordering = ['-created_at']
    list_per_page = 100
    
    fieldsets = (
        ('Stock Information', {
            'fields': ('stock_item', 'quantity', 'change_amount', 'change_type')
        }),
        ('Reference', {
            'fields': ('reference', 'notes')
        }),
        ('Metadata', {
            'fields': ('created_at', 'created_by'),
            'classes': ('collapse',)
        }),
    )
    
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('stock_item', 'created_by')
    
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


@admin.register(Fitter)
class FitterAdmin(admin.ModelAdmin):
    list_display = ['name', 'email', 'phone', 'hourly_rate', 'active']
    search_fields = ['name', 'email', 'phone']
    list_filter = ['active']
    list_editable = ['hourly_rate', 'active']


@admin.register(FactoryWorker)
class FactoryWorkerAdmin(admin.ModelAdmin):
    list_display = ['name', 'email', 'phone', 'hourly_rate', 'active']
    search_fields = ['name', 'email', 'phone']
    list_filter = ['active']
    list_editable = ['hourly_rate', 'active']


@admin.register(Timesheet)
class TimesheetAdmin(admin.ModelAdmin):
    list_display = ['order', 'timesheet_type', 'worker_name', 'date', 'hours', 'hourly_rate', 'total_cost']
    search_fields = ['order__sale_number', 'fitter__name', 'factory_worker__name', 'description']
    list_filter = ['timesheet_type', 'date', 'fitter', 'factory_worker']
    readonly_fields = ['total_cost']
    date_hierarchy = 'date'
    
    def total_cost(self, obj):
        return f"£{obj.total_cost:.2f}"
    total_cost.short_description = 'Total Cost'


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ['order', 'fitter', 'expense_type', 'date', 'amount', 'description']
    search_fields = ['order__sale_number', 'fitter__name', 'description']
    list_filter = ['expense_type', 'date', 'fitter']
    date_hierarchy = 'date'


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'role', 'dark_mode']
    search_fields = ['user__username', 'user__email']
    list_filter = ['dark_mode', 'role']


class PagePermissionInline(admin.TabularInline):
    model = PagePermission
    extra = 0


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ['name', 'description', 'user_count', 'created_at']
    inlines = [PagePermissionInline]

    def user_count(self, obj):
        return obj.users.count()
    user_count.short_description = 'Users'


@admin.register(PagePermission)
class PagePermissionAdmin(admin.ModelAdmin):
    list_display = ['role', 'page_codename', 'can_view', 'can_create', 'can_edit', 'can_delete']
    list_filter = ['role', 'can_view', 'can_create', 'can_edit', 'can_delete']
    list_editable = ['can_view', 'can_create', 'can_edit', 'can_delete']


@admin.register(XeroToken)
class XeroTokenAdmin(admin.ModelAdmin):
    list_display = ['tenant_name', 'tenant_id', 'connected_by', 'is_expired', 'updated_at']
    readonly_fields = ['access_token', 'refresh_token', 'expires_at', 'created_at', 'updated_at']


@admin.register(SyncLog)
class SyncLogAdmin(admin.ModelAdmin):
    list_display = ['script_name', 'ran_at', 'status', 'records_created', 'records_updated', 'errors']
    list_filter = ['script_name', 'status']
    search_fields = ['script_name', 'notes']
    readonly_fields = ['script_name', 'ran_at', 'status', 'records_created', 'records_updated', 'errors', 'notes']


@admin.register(AnthillSale)
class AnthillSaleAdmin(admin.ModelAdmin):
    list_display = ['anthill_activity_id', 'customer_name', 'status', 'category', 'activity_type', 'sale_value', 'activity_date', 'location']
    search_fields = ['anthill_activity_id', 'customer_name', 'contract_number', 'anthill_customer_id']
    list_filter = ['status', 'category', 'location', 'activity_type']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['customer', 'order']


@admin.register(AnthillPayment)
class AnthillPaymentAdmin(admin.ModelAdmin):
    list_display = ['sale', 'payment_type', 'date', 'location', 'user_name', 'amount', 'status']
    search_fields = ['sale__anthill_activity_id', 'sale__customer_name', 'payment_type', 'user_name', 'anthill_payment_id']
    list_filter = ['payment_type', 'status', 'location']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['sale']


# ── Models added below ──────────────────────────────────────────────


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ['name', 'email', 'phone', 'status', 'source', 'anthill_customer_id', 'created_at']
    search_fields = ['name', 'email', 'phone', 'anthill_customer_id']
    list_filter = ['status', 'source']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['converted_to_customer']


@admin.register(AnthillOrderToPlace)
class AnthillOrderToPlaceAdmin(admin.ModelAdmin):
    list_display = ['contract_number', 'customer', 'site', 'assigned_to', 'total_value', 'workflow_status', 'fit_date', 'resolved']
    search_fields = ['contract_number', 'customer', 'address', 'assigned_to']
    list_filter = ['resolved', 'site', 'workflow_status']
    readonly_fields = ['first_seen', 'last_seen']


@admin.register(Designer)
class DesignerAdmin(admin.ModelAdmin):
    list_display = ['name', 'created_at']
    search_fields = ['name']


@admin.register(PNXItem)
class PNXItemAdmin(admin.ModelAdmin):
    list_display = ['barcode', 'matname', 'customer', 'cleng', 'cwidth', 'cnt', 'received', 'ordername']
    search_fields = ['barcode', 'matname', 'customer', 'ordername']
    list_filter = ['received', 'matname']
    raw_id_fields = ['boards_po']


@admin.register(Accessory)
class AccessoryAdmin(admin.ModelAdmin):
    list_display = ['order', 'sku', 'name', 'quantity', 'cost_price', 'billable', 'ordered', 'missing', 'is_allocated']
    search_fields = ['sku', 'name', 'order__sale_number']
    list_filter = ['billable', 'ordered', 'missing', 'is_allocated', 'required']
    raw_id_fields = ['order', 'stock_item']


@admin.register(Schedule)
class ScheduleAdmin(admin.ModelAdmin):
    list_display = ['name', 'scheduled_date', 'status', 'assigned_to', 'auto_generated']
    search_fields = ['name', 'assigned_to']
    list_filter = ['status', 'auto_generated']
    readonly_fields = ['created_date']


@admin.register(Substitution)
class SubstitutionAdmin(admin.ModelAdmin):
    list_display = ['missing_sku', 'missing_name', 'replacement_sku', 'replacement_name', 'quantity', 'created_at']
    search_fields = ['missing_sku', 'missing_name', 'replacement_sku', 'replacement_name']
    readonly_fields = ['created_at']


@admin.register(CSVSkipItem)
class CSVSkipItemAdmin(admin.ModelAdmin):
    list_display = ['sku', 'name', 'order', 'created_at']
    search_fields = ['sku', 'name', 'order__sale_number']
    readonly_fields = ['created_at']
    raw_id_fields = ['order']


@admin.register(SalesAppointment)
class SalesAppointmentAdmin(admin.ModelAdmin):
    list_display = ['customer_name', 'designer', 'event_type', 'appointment_date', 'appointment_time', 'postcode']
    search_fields = ['customer_name', 'designer', 'postcode']
    list_filter = ['event_type', 'designer', 'appointment_date']
    date_hierarchy = 'appointment_date'


@admin.register(WorkflowStageDate)
class WorkflowStageDateAdmin(admin.ModelAdmin):
    list_display = ['order', 'stage', 'completed_date', 'synced_at']
    search_fields = ['order__sale_number']
    list_filter = ['stage']
    raw_id_fields = ['order']


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ['name', 'email', 'phone', 'city', 'country', 'vat_rate', 'is_active']
    search_fields = ['name', 'email', 'phone', 'city']
    list_filter = ['is_active', 'country']


@admin.register(SupplierContact)
class SupplierContactAdmin(admin.ModelAdmin):
    list_display = ['supplier', 'first_name', 'last_name', 'email', 'phone', 'position', 'is_default']
    search_fields = ['first_name', 'last_name', 'email', 'supplier__name']
    list_filter = ['is_default']
    raw_id_fields = ['supplier']


class PurchaseOrderProductInline(admin.TabularInline):
    model = PurchaseOrderProduct
    extra = 0
    fields = ['sku', 'name', 'order_quantity', 'received_quantity', 'order_price', 'line_total']
    readonly_fields = ['line_total']


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ['number', 'supplier_name', 'status', 'total', 'issue_date', 'expected_date', 'received_date']
    search_fields = ['number', 'display_number', 'supplier_name', 'description', 'project_name']
    list_filter = ['status', 'supplier_name']
    readonly_fields = ['created_at', 'last_synced']
    inlines = [PurchaseOrderProductInline]
    raw_id_fields = ['parent_po', 'fitter']


@admin.register(PurchaseOrderProduct)
class PurchaseOrderProductAdmin(admin.ModelAdmin):
    list_display = ['purchase_order', 'sku', 'name', 'order_quantity', 'received_quantity', 'order_price', 'line_total']
    search_fields = ['sku', 'name', 'purchase_order__number']
    list_filter = ['purchase_order__status']
    raw_id_fields = ['purchase_order', 'stock_item']


@admin.register(PurchaseOrderAttachment)
class PurchaseOrderAttachmentAdmin(admin.ModelAdmin):
    list_display = ['purchase_order', 'filename', 'description', 'uploaded_by', 'uploaded_at']
    search_fields = ['filename', 'description', 'purchase_order__number']
    readonly_fields = ['uploaded_at']
    raw_id_fields = ['purchase_order']


@admin.register(PurchaseOrderInvoice)
class PurchaseOrderInvoiceAdmin(admin.ModelAdmin):
    list_display = ['purchase_order', 'invoice_number', 'date', 'amount', 'status']
    search_fields = ['invoice_number', 'purchase_order__number']
    list_filter = ['status']
    readonly_fields = ['uploaded_at']
    raw_id_fields = ['purchase_order']


@admin.register(PurchaseOrderProject)
class PurchaseOrderProjectAdmin(admin.ModelAdmin):
    list_display = ['purchase_order', 'project_type', 'order', 'label', 'sort_order']
    search_fields = ['purchase_order__number', 'label', 'order__sale_number']
    list_filter = ['project_type']
    raw_id_fields = ['purchase_order', 'order']


@admin.register(ProductCustomerAllocation)
class ProductCustomerAllocationAdmin(admin.ModelAdmin):
    list_display = ['product', 'order', 'quantity', 'notes', 'created_at']
    search_fields = ['product__sku', 'order__sale_number', 'notes']
    readonly_fields = ['created_at']
    raw_id_fields = ['product', 'order']


class InvoiceLineItemInline(admin.TabularInline):
    model = InvoiceLineItem
    extra = 0
    fields = ['name', 'quantity', 'rate', 'line_total']
    readonly_fields = ['line_total']


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ['invoice_number', 'client_name', 'date', 'total', 'status', 'payment_status', 'amount_outstanding']
    search_fields = ['invoice_number', 'client_name', 'project_name', 'invoice_reference']
    list_filter = ['status', 'payment_status']
    readonly_fields = ['synced_at', 'created_at', 'updated_at']
    inlines = [InvoiceLineItemInline]
    raw_id_fields = ['customer', 'order']


@admin.register(InvoiceLineItem)
class InvoiceLineItemAdmin(admin.ModelAdmin):
    list_display = ['invoice', 'name', 'quantity', 'rate', 'line_total']
    search_fields = ['name', 'invoice__invoice_number']
    raw_id_fields = ['invoice']


@admin.register(InvoicePayment)
class InvoicePaymentAdmin(admin.ModelAdmin):
    list_display = ['invoice', 'name', 'amount', 'date']
    search_fields = ['invoice__invoice_number', 'name']
    raw_id_fields = ['invoice']


class PurchaseInvoiceLineItemInline(admin.TabularInline):
    model = PurchaseInvoiceLineItem
    extra = 0
    fields = ['description', 'quantity', 'rate', 'line_total', 'order']
    readonly_fields = ['line_total']
    raw_id_fields = ['order']


@admin.register(PurchaseInvoice)
class PurchaseInvoiceAdmin(admin.ModelAdmin):
    list_display = ['invoice_number', 'supplier_name', 'date', 'total', 'status', 'payment_status', 'amount_paid']
    search_fields = ['invoice_number', 'supplier_name']
    list_filter = ['status', 'payment_status']
    readonly_fields = ['created_at', 'updated_at']
    inlines = [PurchaseInvoiceLineItemInline]


@admin.register(PurchaseInvoiceLineItem)
class PurchaseInvoiceLineItemAdmin(admin.ModelAdmin):
    list_display = ['invoice', 'description', 'quantity', 'rate', 'line_total']
    search_fields = ['description', 'invoice__invoice_number']
    raw_id_fields = ['invoice', 'order']


@admin.register(GalleryImage)
class GalleryImageAdmin(admin.ModelAdmin):
    list_display = ['caption', 'order', 'customer', 'uploaded_by', 'uploaded_at']
    search_fields = ['caption', 'order__sale_number', 'customer__name']
    list_filter = ['uploaded_at']
    readonly_fields = ['uploaded_at']
    raw_id_fields = ['order', 'customer', 'uploaded_by']


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ['title', 'priority', 'status', 'submitted_by', 'read_by_admin', 'created_at']
    search_fields = ['title', 'description']
    list_filter = ['priority', 'status', 'read_by_admin']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(ClaimDocument)
class ClaimDocumentAdmin(admin.ModelAdmin):
    list_display = ['title', 'customer_name', 'group_key', 'uploaded_by', 'uploaded_at']
    search_fields = ['title', 'customer_name', 'group_key']
    readonly_fields = ['uploaded_at']
    raw_id_fields = ['uploaded_by', 'downloaded_by']


@admin.register(PriceHistory)
class PriceHistoryAdmin(admin.ModelAdmin):
    list_display = ['stock_item', 'old_price', 'new_price', 'change_source', 'reference', 'created_at', 'created_by']
    search_fields = ['stock_item__sku', 'stock_item__name', 'reference']
    list_filter = ['change_source', 'created_at']
    readonly_fields = ['created_at']
    raw_id_fields = ['stock_item', 'created_by']


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ['user', 'event_type', 'timestamp', 'order', 'purchase_order', 'resolved']
    search_fields = ['description', 'user__username', 'order__sale_number', 'purchase_order__number']
    list_filter = ['event_type', 'resolved']
    readonly_fields = ['timestamp']
    date_hierarchy = 'timestamp'
    raw_id_fields = ['user', 'order', 'purchase_order', 'resolved_by']


@admin.register(RaumplusDraftOrder)
class RaumplusDraftOrderAdmin(admin.ModelAdmin):
    list_display = ['name', 'created_by', 'created_at', 'updated_at']
    search_fields = ['name']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['created_by']
