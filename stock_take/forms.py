from django import forms
from .models import Order, BoardsPO, OSDoor, Accessory, Substitution, CSVSkipItem

class BoardsPOForm(forms.ModelForm):
    class Meta:
        model = BoardsPO
        fields = ['po_number', 'boards_ordered', 'file']
        widgets = {
            'po_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., PO1234'}),
            'boards_ordered': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
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
            'order_date', 'fit_date', 'designer', 'boards_po',
            'job_finished', 'address', 'postcode', 'order_type', 'os_doors_required', 'all_items_ordered',
            'anthill_id', 'workguru_id'
        ]
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-input'}),
            'last_name': forms.TextInput(attrs={'class': 'form-input'}),
            'sale_number': forms.TextInput(attrs={'class': 'form-input'}),
            'customer_number': forms.TextInput(attrs={'class': 'form-input'}),
            'order_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-input'}),
            'fit_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-input'}),
            'designer': forms.Select(attrs={'class': 'form-input'}),
            'boards_po': forms.Select(attrs={'class': 'form-input'}),
            'job_finished': forms.CheckboxInput(attrs={'class': 'checkbox-input'}),
            'address': forms.TextInput(attrs={'class': 'form-input'}),
            'postcode': forms.TextInput(attrs={'class': 'form-input'}),
            'order_type': forms.Select(attrs={'class': 'form-input'}),
            'os_doors_required': forms.CheckboxInput(attrs={'class': 'checkbox-input'}),
            'all_items_ordered': forms.CheckboxInput(attrs={'class': 'checkbox-input'}),
            'anthill_id': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g., 273121'}),
            'workguru_id': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g., 41422'}),
        }
        labels = {
            'customer_number': 'CAD Number',
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


class OSDoorForm(forms.ModelForm):
    class Meta:
        model = OSDoor
        fields = [
            'door_style', 'style_colour', 'item_description',
            'height', 'width', 'colour', 'quantity',
            'po_number', 'ordered', 'received'
        ]
        widgets = {
            'door_style': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., Flush Door'}),
            'style_colour': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., White'}),
            'item_description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Detailed description'}),
            'height': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': 'Height in mm'}),
            'width': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': 'Width in mm'}),
            'colour': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., Oak'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'min': '1'}),
            'po_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'PO Number'}),
            'ordered': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'received': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class AccessoryCSVForm(forms.Form):
    csv_file = forms.FileField(
        label='Upload Accessories CSV',
        help_text='CSV should have columns: Sku, Name, Description, CostPrice, SellPrice, Quantity, Billable. Order will be auto-detected from filename.',
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.csv'})
    )


class SubstitutionForm(forms.ModelForm):
    class Meta:
        model = Substitution
        fields = [
            'missing_sku', 'missing_name', 'replacement_sku'
        ]
        widgets = {
            'missing_sku': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Missing SKU'}),
            'missing_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Missing Item Name'}),
            'replacement_sku': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Replacement SKU'}),
        }


class CSVSkipItemForm(forms.ModelForm):
    class Meta:
        model = CSVSkipItem
        fields = ['sku', 'name']
        widgets = {
            'sku': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'SKU to skip'}),
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Item name'}),
        }
