from django.urls import path
from . import views
from django.contrib.auth import views as auth_views
from .dark_mode_view import toggle_dark_mode
from .location_view import set_location
from .dashboard_view import dashboard
from .product_view import product_detail, add_product, upload_product_image
from .purchase_order_views import purchase_orders_list, purchase_order_detail, purchase_order_save, purchase_order_receive, purchase_order_create, purchase_order_add_product, purchase_order_delete_product, purchase_order_delete_board_items, sync_purchase_orders_stream, suppliers_list, supplier_detail, supplier_save, supplier_create, product_search, purchase_order_download_pdf, purchase_order_send_email, purchase_order_update_status, purchase_order_upload_attachment, purchase_order_delete_attachment, purchase_order_attach_boards_files, create_boards_purchase_order, create_os_doors_purchase_order, purchase_order_delete, purchase_order_list_media_files, purchase_order_attach_media_file, product_add_allocation, product_delete_allocation, order_search, purchase_order_search, purchase_order_toggle_project, po_add_project, po_remove_project, supplier_contact_add, supplier_contact_edit, supplier_contact_delete, supplier_contact_set_default, po_upload_invoice, po_update_invoice, po_delete_invoice
from .customer_views import customers_list, customer_detail, customer_save, customer_delete, customers_bulk_delete, customer_create, customer_merge, sales_list, sale_detail
from .admin_views import admin_users, admin_templates, admin_roles, admin_settings, admin_role_edit, admin_role_toggle_all, impersonate_start, impersonate_stop
from .invoice_views import invoices_list, invoice_detail, sync_invoices_stream
from .ticket_views import tickets_list, ticket_detail, ticket_update_status, ticket_edit, ticket_delete
from .claim_views import claim_service, claim_upload, claim_delete, claim_api_upload, claim_download_zip, claim_file_download
from .cad_views import cad_db_upload, cad_db_download, cad_db_status
from .profile_views import user_profile, user_profile_save, user_change_password
from .xero_views import xero_connect, xero_callback, xero_disconnect, xero_status, xero_api_test, xero_create_customer, xero_customer_search, xero_check_contact
from .lead_views import leads_list, lead_detail, lead_save, lead_delete, leads_bulk_delete, lead_create, lead_merge, lead_convert

urlpatterns = [
    path('', dashboard, name='dashboard'),

    # User Profile
    path('profile/', user_profile, name='user_profile'),
    path('profile/save/', user_profile_save, name='user_profile_save'),
    path('profile/change-password/', user_change_password, name='user_change_password'),
    path('stock/', views.stock_list, name='stock_list'),
    path('import/', views.import_csv, name='import_csv'),
    path('export/', views.export_csv, name='export_csv'),
    path('update/<int:item_id>/', views.update_item, name='update_item'),
    path('update-product-quantity/', views.update_product_quantity, name='update_product_quantity'),
    
    # Invoices
    path('invoices/', invoices_list, name='invoices_list'),
    path('invoices/sync/', sync_invoices_stream, name='sync_invoices'),
    path('invoices/<int:invoice_id>/', invoice_detail, name='invoice_detail'),

    # Purchase Orders
    path('purchase-orders/', purchase_orders_list, name='purchase_orders_list'),
    path('purchase-orders/create/', purchase_order_create, name='purchase_order_create'),
    path('purchase-orders/sync/', sync_purchase_orders_stream, name='sync_purchase_orders'),
    path('purchase-order/<int:po_id>/', purchase_order_detail, name='purchase_order_detail'),
    path('purchase-order/<int:po_id>/save/', purchase_order_save, name='purchase_order_save'),
    path('purchase-order/<int:po_id>/toggle-project/', purchase_order_toggle_project, name='purchase_order_toggle_project'),
    path('purchase-order/<int:po_id>/add-project/', po_add_project, name='po_add_project'),
    path('purchase-order/<int:po_id>/remove-project/<int:project_id>/', po_remove_project, name='po_remove_project'),
    path('purchase-order/<int:po_id>/receive/', purchase_order_receive, name='purchase_order_receive'),
    path('purchase-order/<int:po_id>/add-product/', purchase_order_add_product, name='purchase_order_add_product'),
    path('purchase-order/<int:po_id>/download-pdf/', purchase_order_download_pdf, name='purchase_order_download_pdf'),
    path('purchase-order/<int:po_id>/send-email/', purchase_order_send_email, name='purchase_order_send_email'),
    path('purchase-order/<int:po_id>/update-status/', purchase_order_update_status, name='purchase_order_update_status'),
    path('api/product-search/', product_search, name='product_search'),
    path('api/order-search/', order_search, name='order_search'),
    path('api/purchase-order-search/', purchase_order_search, name='purchase_order_search'),
    path('purchase-order/<int:po_id>/delete-product/<int:product_id>/', purchase_order_delete_product, name='purchase_order_delete_product'),
    path('purchase-order/<int:po_id>/delete-board-items/', purchase_order_delete_board_items, name='purchase_order_delete_board_items'),
    path('purchase-order/<int:po_id>/upload-attachment/', purchase_order_upload_attachment, name='purchase_order_upload_attachment'),
    path('purchase-order/<int:po_id>/delete-attachment/<int:attachment_id>/', purchase_order_delete_attachment, name='purchase_order_delete_attachment'),
    path('purchase-order/<int:po_id>/attach-boards-files/', purchase_order_attach_boards_files, name='purchase_order_attach_boards_files'),
    path('purchase-order/<int:po_id>/delete/', purchase_order_delete, name='purchase_order_delete'),
    path('purchase-order/<int:po_id>/media-files/', purchase_order_list_media_files, name='purchase_order_list_media_files'),
    path('purchase-order/<int:po_id>/attach-media-file/', purchase_order_attach_media_file, name='purchase_order_attach_media_file'),
    path('purchase-order/<int:po_id>/product/<int:product_id>/add-allocation/', product_add_allocation, name='product_add_allocation'),
    path('purchase-order/<int:po_id>/product/<int:product_id>/delete-allocation/<int:allocation_id>/', product_delete_allocation, name='product_delete_allocation'),
    path('purchase-order/<int:po_id>/upload-invoice/', po_upload_invoice, name='po_upload_invoice'),
    path('purchase-order/<int:po_id>/update-invoice/<int:invoice_id>/', po_update_invoice, name='po_update_invoice'),
    path('purchase-order/<int:po_id>/delete-invoice/<int:invoice_id>/', po_delete_invoice, name='po_delete_invoice'),
    
    # Suppliers
    path('suppliers/', suppliers_list, name='suppliers_list'),
    path('suppliers/create/', supplier_create, name='supplier_create'),
    path('supplier/<int:supplier_id>/', supplier_detail, name='supplier_detail'),
    path('supplier/<int:supplier_id>/save/', supplier_save, name='supplier_save'),
    path('supplier/<int:supplier_id>/contacts/add/', supplier_contact_add, name='supplier_contact_add'),
    path('supplier/<int:supplier_id>/contacts/<int:contact_id>/edit/', supplier_contact_edit, name='supplier_contact_edit'),
    path('supplier/<int:supplier_id>/contacts/<int:contact_id>/delete/', supplier_contact_delete, name='supplier_contact_delete'),
    path('supplier/<int:supplier_id>/contacts/<int:contact_id>/set-default/', supplier_contact_set_default, name='supplier_contact_set_default'),
    
    # Customers
    path('sales/', sales_list, name='sales_list'),
    path('sale/<int:pk>/', sale_detail, name='sale_detail'),
    path('customers/', customers_list, name='customers_list'),
    path('customers/create/', customer_create, name='customer_create'),
    path('customer/<str:customer_name>/', customer_detail, name='customer_detail'),
    path('customer/<int:pk>/save/', customer_save, name='customer_save'),
    path('customer/<int:pk>/delete/', customer_delete, name='customer_delete'),
    path('customers/bulk-delete/', customers_bulk_delete, name='customers_bulk_delete'),
    path('customers/merge/', customer_merge, name='customer_merge'),
    
    # Leads
    path('leads/', leads_list, name='leads_list'),
    path('leads/create/', lead_create, name='lead_create'),
    path('lead/<int:pk>/', lead_detail, name='lead_detail'),
    path('lead/<int:pk>/save/', lead_save, name='lead_save'),
    path('lead/<int:pk>/delete/', lead_delete, name='lead_delete'),
    path('lead/<int:pk>/convert/', lead_convert, name='lead_convert'),
    path('leads/bulk-delete/', leads_bulk_delete, name='leads_bulk_delete'),
    path('leads/merge/', lead_merge, name='lead_merge'),
    
    # Import management
    path('import-history/', views.import_history, name='import_history'),
    path('import/delete/<int:import_id>/', views.delete_import, name='delete_import'),
    path('review-orphaned-items/', views.review_orphaned_items, name='review_orphaned_items'),
    
    # Category management
    path('categories/', views.category_list, name='category_list'),
    path('categories/create/', views.category_create, name='category_create'),
    path('categories/edit/<int:category_id>/', views.category_edit, name='category_edit'),
    path('categories/delete/<int:category_id>/', views.category_delete, name='category_delete'),
    
    # Stock take group management
    path('stock-take-groups/create/', views.stock_take_group_create, name='stock_take_group_create'),
    path('get-unassigned-items/', views.get_unassigned_items, name='get_unassigned_items'),
    path('delete-category/<int:category_id>/', views.delete_category, name='delete_category'),
    path('assign-item-to-group/', views.assign_item_to_group, name='assign_item_to_group'),
    

    # Schedule management
    path('schedules/', views.schedule_list, name='schedule_list'),
    path('schedules/completed/', views.completed_stock_takes, name='completed_stock_takes'),
    path('schedules/create/', views.schedule_create, name='schedule_create'),
    path('schedules/edit/<int:schedule_id>/', views.schedule_edit, name='schedule_edit'),
    path('schedules/update-status/<int:schedule_id>/', views.schedule_update_status, name='schedule_update_status'),
    path('schedules/delete/<int:schedule_id>/', views.delete_schedule, name='delete_schedule'),
    
    # Stock take functionality
    path('stock-take/<int:schedule_id>/', views.stock_take_detail, name='stock_take_detail'),
    path('stock-take/<int:schedule_id>/export/', views.export_stock_take_csv, name='export_stock_take_csv'),
    path('stock-take/update-count/', views.update_stock_count, name='update_stock_count'),
    path('stock-take-groups/delete/<int:group_id>/', views.delete_stock_take_group, name='delete_stock_take_group'),
    
    # Ordering page
    path('ordering/', views.ordering, name='ordering'),
    path('ordering/load-order-details/<str:sale_number>/', views.load_order_details_ajax, name='load_order_details_ajax'),
    path('ordering/load-indicators/', views.load_order_indicators_ajax, name='load_order_indicators_ajax'),
    path('search-customers/', views.search_customers, name='search_customers'),
    path('add-designer/', views.add_designer, name='add_designer'),
    path('search/', views.search_orders, name='search_orders'),
    path('global-search/', views.global_search, name='global_search'),
    path('material-report/', views.material_report, name='material_report'),
    path('material-shortage/', views.material_shortage, name='material_shortage'),
    path('raumplus-storage/', views.raumplus_storage, name='raumplus_storage'),
    path('costing-report/', views.costing_report, name='costing_report'),
    path('substitutions/', views.substitutions, name='substitutions'),
    path('substitution/delete/<int:substitution_id>/', views.delete_substitution, name='delete_substitution'),
    path('substitution/edit/<int:substitution_id>/', views.edit_substitution, name='edit_substitution'),
    path('ordering/create-po/', views.create_boards_po, name='create_boards_po'),
    path('stock-take/boards-po/<int:boards_po_id>/update-boards-ordered/', views.update_boards_ordered, name='update_boards_ordered'),
    path('stock-take/boards-po/<int:boards_po_id>/update-po-number/', views.update_po_number, name='update_po_number'),
    path('stock-take/boards-po/<int:boards_po_id>/replace-pnx/', views.replace_pnx_file, name='replace_pnx_file'),
    path('stock-take/boards-po/<int:boards_po_id>/preview-pnx/', views.preview_pnx_file, name='preview_pnx_file'),
    path('stock-take/boards-po/<int:boards_po_id>/preview-csv/', views.preview_csv_file, name='preview_csv_file'),
    path('stock-take/boards-po/<int:boards_po_id>/update-both-files/', views.update_both_files, name='update_both_files'),
    path('stock-take/boards-po/<int:boards_po_id>/update-pnx/', views.update_pnx_file, name='update_pnx_file'),
    path('stock-take/boards-po/<int:boards_po_id>/update-csv/', views.update_csv_file, name='update_csv_file'),
    path('stock-take/boards-po/<int:boards_po_id>/generate-csv/', views.generate_csv_file, name='generate_csv_file'),
    path('stock-take/boards-po/<int:boards_po_id>/download-csv/', views.download_pnx_as_csv_boardspo, name='download_pnx_as_csv'),
    path('stock-take/boards-po/<int:boards_po_id>/reimport-pnx/', views.reimport_pnx, name='reimport_pnx'),
    path('stock-take/boards-po/<int:boards_po_id>/delete/', views.delete_boards_po, name='delete_boards_po'),
    path('stock-take/accessory-csv/<int:csv_id>/preview/', views.preview_accessory_csv, name='preview_accessory_csv'),
    path('stock-take/accessory-csv/<int:csv_id>/delete/', views.delete_accessory_csv, name='delete_accessory_csv'),
    path('stock-take/pnx-item/<int:pnx_item_id>/update/', views.update_pnx_item, name='update_pnx_item'),
    path('stock-take/update-pnx-dimensions/', views.update_pnx_dimensions, name='update_pnx_dimensions'),
    path('stock-take/add-board-item/', views.add_board_item, name='add_board_item'),
    path('stock-take/boards-po/<int:boards_po_id>/reimport-pnx/', views.reimport_pnx, name='reimport_pnx'),
    path('stock-take/pnx-item/<int:pnx_item_id>/update-received/', views.update_pnx_received, name='update_pnx_received'),
    path('stock-take/update-pnx-batch/', views.update_pnx_batch, name='update_pnx_batch'),
    path('stock-take/update-os-doors-batch/', views.update_os_doors_batch, name='update_os_doors_batch'),
    path('stock-take/delete-os-doors-batch/', views.delete_os_doors_batch, name='delete_os_doors_batch'),
    path('stock-take/delete-accessories-batch/', views.delete_accessories_batch, name='delete_accessories_batch'),
    path('stock-take/add-accessories-to-po/', views.add_accessories_to_po, name='add_accessories_to_po'),
    path('stock-take/boards-po/<int:boards_po_id>/replace-pnx/', views.replace_pnx_file, name='replace_pnx_file'),
    path('ordering/upload-accessories-csv/', views.upload_accessories_csv, name='upload_accessories_csv'),
    path('order/<int:order_id>/', views.order_details, name='order_details'),
    path('order/<int:order_id>/delete/', views.order_delete, name='order_delete'),
    path('order/<int:order_id>/update-customer/', views.update_customer_info, name='update_customer_info'),
    path('order/<int:order_id>/update-sale/', views.update_sale_info, name='update_sale_info'),
    path('order/<int:order_id>/update-order-type/', views.update_order_type, name='update_order_type'),
    path('order/<int:order_id>/update-boards-po/', views.update_boards_po, name='update_boards_po'),
    path('order/<int:order_id>/add-additional-boards-po/', views.add_additional_boards_po, name='add_additional_boards_po'),
    path('order/<int:order_id>/remove-additional-boards-po/', views.remove_additional_boards_po, name='remove_additional_boards_po'),
    path('order/<int:order_id>/update-job-checkbox/', views.update_job_checkbox, name='update_job_checkbox'),
    path('order/<int:order_id>/update-financial/', views.update_order_financial, name='update_order_financial'),
    path('order/<int:order_id>/save-all-financials/', views.save_all_order_financials, name='save_all_order_financials'),
    path('order/<int:order_id>/recalculate-financials/', views.recalculate_order_financials, name='recalculate_order_financials'),
    path('order/<int:order_id>/download-processed-csv/', views.download_processed_csv, name='download_processed_csv'),
    path('order/<int:order_id>/download-current-accessories-csv/', views.download_current_accessories_csv, name='download_current_accessories_csv'),
    path('order/<int:order_id>/download-pnx-csv/', views.download_pnx_as_csv, name='download_pnx_as_csv'),
    path('order/<int:order_id>/summary-document/', views.generate_summary_document, name='generate_summary_document'),
    path('order/<int:order_id>/generate-pnx/', views.generate_and_attach_pnx, name='generate_and_attach_pnx'),
    path('order/<int:order_id>/regenerate-boards-files/', views.regenerate_boards_po_files, name='regenerate_boards_po_files'),
    path('order/<int:order_id>/update-boards-po-files/', views.update_boards_po_files, name='update_boards_po_files'),
    path('order/<int:order_id>/create-boards-purchase-order/', create_boards_purchase_order, name='create_boards_purchase_order'),
    path('order/<int:order_id>/create-os-doors-purchase-order/', create_os_doors_purchase_order, name='create_os_doors_purchase_order'),
    path('order/<int:order_id>/confirm-pnx/', views.confirm_pnx_generation, name='confirm_pnx_generation'),
    path('delete-pnx-items/', views.delete_pnx_items, name='delete_pnx_items'),
    path('order/<int:order_id>/generate-accessories-csv/', views.generate_and_upload_accessories_csv, name='generate_and_upload_accessories_csv'),
    path('accessory/delete/<int:accessory_id>/', views.delete_accessory, name='delete_accessory'),
    path('order/<int:order_id>/allocate-accessories/', views.allocate_accessories, name='allocate_accessories'),
    path('order/<int:order_id>/update-os-doors-po/', views.update_os_doors_po, name='update_os_doors_po'),
    path('order/<int:order_id>/delete-all-accessories/', views.delete_all_accessories, name='delete_all_accessories'),
    path('order/<int:order_id>/remove-csv/<str:csv_type>/', views.remove_order_csv, name='remove_order_csv'),
    path('order/<int:order_id>/resolve-missing-items/', views.resolve_missing_items, name='resolve_missing_items'),
    path('order/<int:order_id>/add-substitution/', views.add_substitution, name='add_substitution'),
    path('order/<int:order_id>/add-skip-item/', views.add_skip_item, name='add_skip_item'),
    path('skip-item/delete/<int:skip_item_id>/', views.delete_skip_item, name='delete_skip_item'),
    path('stock-take/search-stock-items/', views.search_stock_items, name='search_stock_items'),
    path('stock-take/swap-accessory/<int:accessory_id>/', views.swap_accessory, name='swap_accessory'),
    path('stock-take/update-accessory-quantities/', views.update_accessory_quantities, name='update_accessory_quantities'),
    path('stock-take/add-accessory-item/<int:order_id>/', views.add_accessory_item, name='add_accessory_item'),
    path('stock-take/regenerate-csv/<int:order_id>/', views.regenerate_csv, name='regenerate_csv'),
    path('order/<int:order_id>/save-cut-size/<int:accessory_id>/', views.save_cut_size, name='save_cut_size'),
    path('boards-summary/', views.boards_summary, name='boards_summary'),
    path('os-doors-summary/', views.os_doors_summary, name='os_doors_summary'),
    path('remedials/', views.remedials, name='remedials'),
    path('remedial-report/', views.remedial_report, name='remedial_report'),
    
    # Stock items manager
    path('stock-items-manager/', views.stock_items_manager, name='stock_items_manager'),
    path('stock-items/update-batch/', views.update_stock_items_batch, name='update_stock_items_batch'),
    
    # Fit board
    path('fit-board/', views.fit_board, name='fit_board'),
    path('fit-board/add-appointment/', views.add_fit_appointment, name='add_fit_appointment'),
    path('fit-board/update-status/<int:appointment_id>/', views.update_fit_status, name='update_fit_status'),
    path('fit-board/update-order-status/<int:order_id>/', views.update_order_fit_status, name='update_order_fit_status'),
    path('fit-board/delete-appointment/<int:appointment_id>/', views.delete_fit_appointment, name='delete_fit_appointment'),
    path('fit-board/move-appointment/<int:appointment_id>/', views.move_fit_appointment, name='move_fit_appointment'),
    path('fit-board/bulk-import/', views.bulk_import_fit_dates, name='bulk_import_fit_dates'),
    
    # Search APIs
    path('search-orders-api/', views.search_orders_api, name='search_orders_api'),
    path('search-remedials-api/', views.search_remedials_api, name='search_remedials_api'),
    
    # Workflow page
    path('workflow/', views.workflow, name='workflow'),
    path('workflow/stage/save/', views.save_workflow_stage, name='save_workflow_stage'),
    path('workflow/stage/<int:stage_id>/', views.get_workflow_stage, name='get_workflow_stage'),
    path('workflow/stage/<int:stage_id>/delete/', views.delete_workflow_stage, name='delete_workflow_stage'),
    path('workflow/stage/<int:stage_id>/move/', views.move_workflow_stage, name='move_workflow_stage'),
    path('workflow/task/save/', views.save_workflow_task, name='save_workflow_task'),
    path('workflow/task/<int:task_id>/delete/', views.delete_workflow_task, name='delete_workflow_task'),
    
    # Order workflow operations
    path('order/<int:order_id>/update-workflow-stage/', views.update_order_workflow_stage, name='update_order_workflow_stage'),
    path('order/<int:order_id>/task/<int:task_id>/update/', views.update_task_completion, name='update_task_completion'),
    path('order/<int:order_id>/workflow/progress/', views.progress_to_next_stage, name='progress_to_next_stage'),
    path('order/<int:order_id>/workflow/revert/', views.revert_to_previous_stage, name='revert_to_previous_stage'),
    
    # Map page
    path('map/', views.map_view, name='map'),
    
    # Timesheet and Expense APIs
    path('api/get-fitters/', views.get_fitters, name='get_fitters'),
    path('api/get-factory-workers/', views.get_factory_workers, name='get_factory_workers'),
    path('api/add-fitter/', views.add_fitter, name='add_fitter'),
    path('api/add-factory-worker/', views.add_factory_worker, name='add_factory_worker'),
    path('api/factory-worker/<int:worker_id>/update/', views.update_factory_worker, name='update_factory_worker'),
    path('api/factory-worker/<int:worker_id>/delete/', views.delete_factory_worker, name='delete_factory_worker'),
    path('api/fitter/<int:fitter_id>/update/', views.update_fitter, name='update_fitter'),
    path('api/fitter/<int:fitter_id>/delete/', views.delete_fitter, name='delete_fitter'),
    path('order/<int:order_id>/add-timesheet/', views.add_timesheet, name='add_timesheet'),
    path('order/<int:order_id>/add-multiple-timesheets/', views.add_multiple_timesheets, name='add_multiple_timesheets'),
    path('timesheet/<int:timesheet_id>/delete/', views.delete_timesheet, name='delete_timesheet'),
    path('expense/<int:expense_id>/delete/', views.delete_expense, name='delete_expense'),
    
    # Timesheets
    path('timesheets/', views.timesheets, name='timesheets'),

    # Dark mode / location toggles
    path('toggle-dark-mode/', toggle_dark_mode, name='toggle_dark_mode'),
    path('set-location/', set_location, name='set_location'),

    # Product detail
    path('product/add/', add_product, name='add_product'),
    path('product/<int:item_id>/', product_detail, name='product_detail'),
    path('product/<int:item_id>/upload-image/', upload_product_image, name='upload_product_image'),

    # Admin pages
    path('admin-panel/users/', admin_users, name='admin_users'),
    path('admin-panel/templates/', admin_templates, name='admin_templates'),
    path('admin-panel/roles/', admin_roles, name='admin_roles'),
    path('admin-panel/roles/<int:role_id>/edit/', admin_role_edit, name='admin_role_edit'),
    path('admin-panel/roles/<int:role_id>/toggle-all/', admin_role_toggle_all, name='admin_role_toggle_all'),
    path('admin-panel/impersonate/<int:user_id>/', impersonate_start, name='impersonate_start'),
    path('admin-panel/impersonate/stop/', impersonate_stop, name='impersonate_stop'),
    path('admin-panel/settings/', admin_settings, name='admin_settings'),

    # Password reset views
    path('password_reset/', auth_views.PasswordResetView.as_view(), name='password_reset'),
    path('password_reset/done/', auth_views.PasswordResetDoneView.as_view(), name='password_reset_done'),
    path('reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('reset/done/', auth_views.PasswordResetCompleteView.as_view(), name='password_reset_complete'),

    # Tickets
    path('tickets/', tickets_list, name='tickets_list'),
    path('tickets/<int:ticket_id>/', ticket_detail, name='ticket_detail'),
    path('tickets/<int:ticket_id>/update-status/', ticket_update_status, name='ticket_update_status'),
    path('tickets/<int:ticket_id>/edit/', ticket_edit, name='ticket_edit'),
    path('tickets/<int:ticket_id>/delete/', ticket_delete, name='ticket_delete'),

    # Claim Service
    path('claims/', claim_service, name='claim_service'),
    path('claims/upload/', claim_upload, name='claim_upload'),
    path('claims/api/upload/', claim_api_upload, name='claim_api_upload'),
    path('claims/<int:doc_id>/delete/', claim_delete, name='claim_delete'),
    path('claims/<int:doc_id>/download/', claim_file_download, name='claim_file_download'),
    path('claims/download/<path:group_key>/', claim_download_zip, name='claim_download_zip'),

    # Xero Integration
    path('xero/connect/', xero_connect, name='xero_connect'),
    path('xero/callback/', xero_callback, name='xero_callback'),
    path('xero/disconnect/', xero_disconnect, name='xero_disconnect'),
    path('xero/status/', xero_status, name='xero_status'),
    path('xero/api/test/', xero_api_test, name='xero_api_test'),
    path('xero/api/create-customer/', xero_create_customer, name='xero_create_customer'),
    path('xero/api/customer-search/', xero_customer_search, name='xero_customer_search'),
    path('xero/api/check-contact/', xero_check_contact, name='xero_check_contact'),

    # CAD Database API
    path('api/cad-db/upload/', cad_db_upload, name='cad_db_upload'),
    path('api/cad-db/download/', cad_db_download, name='cad_db_download'),
    path('api/cad-db/status/', cad_db_status, name='cad_db_status'),
]
