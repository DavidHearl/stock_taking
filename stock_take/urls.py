from django.urls import path
from . import views

urlpatterns = [
    path('', views.stock_list, name='stock_list'),
    path('import/', views.import_csv, name='import_csv'),
    path('export/', views.export_csv, name='export_csv'),
    path('update/<int:item_id>/', views.update_item, name='update_item'),
    
    # Category management
    path('categories/', views.category_list, name='category_list'),
    path('categories/create/', views.category_create, name='category_create'),
    path('categories/delete/<int:category_id>/', views.category_delete, name='category_delete'),
    
    # Schedule management
    path('schedules/', views.schedule_list, name='schedule_list'),
    path('schedules/create/', views.schedule_create, name='schedule_create'),
    path('schedules/update-status/<int:schedule_id>/', views.schedule_update_status, name='schedule_update_status'),
]