from django.urls import path
from . import views

urlpatterns = [
    path('', views.stock_list, name='stock_list'),
    path('import/', views.import_csv, name='import_csv'),
    path('export/', views.export_csv, name='export_csv'),
    path('update/<int:item_id>/', views.update_item, name='update_item'),
    

    # Import management
    path('import-history/', views.import_history, name='import_history'),
    path('import/delete/<int:import_id>/', views.delete_import, name='delete_import'),
    
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
    path('search/', views.search_orders, name='search_orders'),
    path('material-report/', views.material_report, name='material_report'),
    path('substitutions/', views.substitutions, name='substitutions'),
    path('substitution/delete/<int:substitution_id>/', views.delete_substitution, name='delete_substitution'),
    path('substitution/edit/<int:substitution_id>/', views.edit_substitution, name='edit_substitution'),
    path('ordering/create-po/', views.create_boards_po, name='create_boards_po'),
    path('stock-take/boards-po/<int:boards_po_id>/update-boards-ordered/', views.update_boards_ordered, name='update_boards_ordered'),
    path('stock-take/boards-po/<int:boards_po_id>/replace-pnx/', views.replace_pnx_file, name='replace_pnx_file'),
    path('ordering/upload-accessories-csv/', views.upload_accessories_csv, name='upload_accessories_csv'),
    path('order/<int:order_id>/', views.order_details, name='order_details'),
    path('order/<int:order_id>/download-processed-csv/', views.download_processed_csv, name='download_processed_csv'),
    path('accessory/delete/<int:accessory_id>/', views.delete_accessory, name='delete_accessory'),
    path('order/<int:order_id>/update-os-doors-po/', views.update_os_doors_po, name='update_os_doors_po'),
    path('order/<int:order_id>/delete-all-accessories/', views.delete_all_accessories, name='delete_all_accessories'),
    path('order/<int:order_id>/remove-csv/<str:csv_type>/', views.remove_order_csv, name='remove_order_csv'),
    path('order/<int:order_id>/resolve-missing-items/', views.resolve_missing_items, name='resolve_missing_items'),
    path('order/<int:order_id>/add-substitution/', views.add_substitution, name='add_substitution'),
    path('order/<int:order_id>/add-skip-item/', views.add_skip_item, name='add_skip_item'),
    path('skip-item/delete/<int:skip_item_id>/', views.delete_skip_item, name='delete_skip_item'),
    
    # Map page
    path('map/', views.map_view, name='map'),
]
