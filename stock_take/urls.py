from django.urls import path
from . import views
from django.contrib.auth import views as auth_views

urlpatterns = [
    path('', views.stock_list, name='stock_list'),
    path('import/', views.import_csv, name='import_csv'),
    path('export/', views.export_csv, name='export_csv'),
    path('update/<int:item_id>/', views.update_item, name='update_item'),
    

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
    path('search/', views.search_orders, name='search_orders'),
    path('material-report/', views.material_report, name='material_report'),
    path('material-shortage/', views.material_shortage, name='material_shortage'),
    path('raumplus-storage/', views.raumplus_storage, name='raumplus_storage'),
    path('substitutions/', views.substitutions, name='substitutions'),
    path('substitution/delete/<int:substitution_id>/', views.delete_substitution, name='delete_substitution'),
    path('substitution/edit/<int:substitution_id>/', views.edit_substitution, name='edit_substitution'),
    path('ordering/create-po/', views.create_boards_po, name='create_boards_po'),
    path('stock-take/boards-po/<int:boards_po_id>/update-boards-ordered/', views.update_boards_ordered, name='update_boards_ordered'),
    path('stock-take/pnx-item/<int:pnx_item_id>/update-received/', views.update_pnx_received, name='update_pnx_received'),
    path('stock-take/update-pnx-batch/', views.update_pnx_batch, name='update_pnx_batch'),
    path('stock-take/update-os-doors-batch/', views.update_os_doors_batch, name='update_os_doors_batch'),
    path('stock-take/boards-po/<int:boards_po_id>/replace-pnx/', views.replace_pnx_file, name='replace_pnx_file'),
    path('ordering/upload-accessories-csv/', views.upload_accessories_csv, name='upload_accessories_csv'),
    path('order/<int:order_id>/', views.order_details, name='order_details'),
    path('order/<int:order_id>/download-processed-csv/', views.download_processed_csv, name='download_processed_csv'),
    path('order/<int:order_id>/download-current-accessories-csv/', views.download_current_accessories_csv, name='download_current_accessories_csv'),
    path('order/<int:order_id>/download-pnx-csv/', views.download_pnx_as_csv, name='download_pnx_as_csv'),
    path('order/<int:order_id>/push-to-workguru/', views.push_accessories_to_workguru, name='push_accessories_to_workguru'),
    path('order/<int:order_id>/generate-pnx/', views.generate_and_attach_pnx, name='generate_and_attach_pnx'),
    path('order/<int:order_id>/generate-accessories-csv/', views.generate_and_upload_accessories_csv, name='generate_and_upload_accessories_csv'),
    path('accessory/delete/<int:accessory_id>/', views.delete_accessory, name='delete_accessory'),
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

    # Password reset views
    path('password_reset/', auth_views.PasswordResetView.as_view(), name='password_reset'),
    path('password_reset/done/', auth_views.PasswordResetDoneView.as_view(), name='password_reset_done'),
    path('reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('reset/done/', auth_views.PasswordResetCompleteView.as_view(), name='password_reset_complete'),
]
