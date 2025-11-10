from django import forms
from .models import Order

class OrderForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = [
            'first_name', 'last_name', 'sale_number', 'customer_number',
            'order_date', 'fit_date', 'boards_po'
        ]
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'w-full px-2 py-1 border rounded'}),
            'last_name': forms.TextInput(attrs={'class': 'w-full px-2 py-1 border rounded'}),
            'sale_number': forms.TextInput(attrs={'class': 'w-full px-2 py-1 border rounded'}),
            'customer_number': forms.TextInput(attrs={'class': 'w-full px-2 py-1 border rounded'}),
            'order_date': forms.DateInput(attrs={'type': 'date', 'class': 'w-full px-2 py-1 border rounded'}),
            'fit_date': forms.DateInput(attrs={'type': 'date', 'class': 'w-full px-2 py-1 border rounded'}),
            'boards_po': forms.TextInput(attrs={'class': 'w-full px-2 py-1 border rounded'}),
        }

    def clean_sale_number(self):
        sale_number = self.cleaned_data['sale_number']
        if len(sale_number) != 6 or not sale_number.isdigit():
            raise forms.ValidationError('Sale Number must be a 6 digit number.')
        return sale_number

    def clean_customer_number(self):
        customer_number = self.cleaned_data['customer_number']
        if len(customer_number) != 6 or not customer_number.isdigit() or not customer_number.startswith('0'):
            raise forms.ValidationError('Customer Number must be a 6 digit number starting with 0.')
        return customer_number

    def clean_boards_po(self):
        boards_po = self.cleaned_data['boards_po']
        if not boards_po.startswith('PO') or len(boards_po) != 6 or not boards_po[2:].isdigit():
            raise forms.ValidationError('Boards PO must be PO followed by 4 numbers.')
        return boards_po
