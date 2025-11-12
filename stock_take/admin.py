from django.contrib import admin
from .models import BoardsPO, Order, OSDoor

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
