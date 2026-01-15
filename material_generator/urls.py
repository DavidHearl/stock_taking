from django.urls import path
from . import views
from . import db_check_view

urlpatterns = [
    path('', views.generate_materials, name='generate_materials'),
    path('generate-pnx/', views.generate_pnx, name='generate_pnx'),
    path('generate-csv/', views.generate_csv, name='generate_csv'),
    path('check-database/', db_check_view.check_database, name='check_database'),
]
