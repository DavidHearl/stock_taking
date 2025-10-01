from django.urls import path
from . import views

urlpatterns = [
    path('', views.stock_list, name='stock_list'),
    path('import/', views.import_csv, name='import_csv'),
    path('export/', views.export_csv, name='export_csv'),
    path('update/<int:item_id>/', views.update_item, name='update_item'),
    
    # Import management
    path('import/delete/<int:import_id>/', views.delete_import, name='delete_import'),
    
    # Category management
    path('categories/', views.category_list, name='category_list'),
    path('categories/create/', views.category_create, name='category_create'),
    path('categories/delete/<int:category_id>/', views.category_delete, name='category_delete'),
    
    # Stock take group management
    path('stock-take-groups/create/', views.stock_take_group_create, name='stock_take_group_create'),
    path('get-unassigned-items/', views.get_unassigned_items, name='get_unassigned_items'),
    path('delete-category/<int:category_id>/', views.delete_category, name='delete_category'),
    path('assign-item-to-group/', views.assign_item_to_group, name='assign_item_to_group'),
    
    # Schedule management
    path('schedules/', views.schedule_list, name='schedule_list'),
    path('schedules/create/', views.schedule_create, name='schedule_create'),
    path('schedules/update-status/<int:schedule_id>/', views.schedule_update_status, name='schedule_update_status'),
    
    # Stock take functionality
    path('stock-take/<int:schedule_id>/', views.stock_take_detail, name='stock_take_detail'),
    path('stock-take/<int:schedule_id>/export/', views.export_stock_take_csv, name='export_stock_take_csv'),
    path('stock-take/update-count/', views.update_stock_count, name='update_stock_count'),
    path('stock-take-groups/delete/<int:group_id>/', views.delete_stock_take_group, name='delete_stock_take_group'),
]