from django import forms
from .models import Order, BoardsPO

class BoardsPOForm(forms.ModelForm):
    class Meta:
        model = BoardsPO
        fields = ['po_number', 'file']
        widgets = {
            'po_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., PO1234'}),
            'file': forms.FileInput(attrs={'class': 'form-control'}),
        }

    def clean_po_number(self):
        po_number = self.cleaned_data['po_number']
        if not po_number.startswith('PO'):
            raise forms.ValidationError('PO number must start with "PO".')
        return po_number

class OrderForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = [
            'first_name', 'last_name', 'sale_number', 'customer_number',
            'order_date', 'fit_date', 'boards_po', 'boards_ordered'
        ]
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'w-full px-2 py-1 border rounded'}),
            'last_name': forms.TextInput(attrs={'class': 'w-full px-2 py-1 border rounded'}),
            'sale_number': forms.TextInput(attrs={'class': 'w-full px-2 py-1 border rounded'}),
            'customer_number': forms.TextInput(attrs={'class': 'w-full px-2 py-1 border rounded'}),
            'order_date': forms.DateInput(attrs={'type': 'date', 'class': 'w-full px-2 py-1 border rounded'}),
            'fit_date': forms.DateInput(attrs={'type': 'date', 'class': 'w-full px-2 py-1 border rounded'}),
            'boards_po': forms.Select(attrs={'class': 'w-full px-2 py-1 border rounded'}),
            'boards_ordered': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
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
