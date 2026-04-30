from django import forms
from django.db.models import Q
from .models import Order, BoardsPO, OSDoor, Accessory, Substitution, CSVSkipItem, RaumplusOrderingRule, StockItem

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
            'customer', 'first_name', 'last_name', 'sale_number', 'customer_number',
            'order_date', 'fit_date', 'designer', 'boards_po',
            'job_finished', 'address', 'postcode', 'order_type', 'os_doors_required', 'all_items_ordered',
            'anthill_id', 'total_value_inc_vat'
        ]
        widgets = {
            'customer': forms.HiddenInput(),
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
            'total_value_inc_vat': forms.NumberInput(attrs={'class': 'form-input', 'placeholder': 'e.g., 5000.00', 'step': '0.01'}),
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
            'height', 'width', 'colour', 'quantity', 'cost_price',
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
            'cost_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0', 'placeholder': '0.00'}),
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
            'missing_sku', 'missing_name', 'replacement_sku', 'replacement_name'
        ]
        widgets = {
            'missing_sku': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Missing SKU'}),
            'missing_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Missing Item Name'}),
            'replacement_sku': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Replacement SKU'}),
            'replacement_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Replacement Item Name'}),
        }


class CSVSkipItemForm(forms.ModelForm):
    class Meta:
        model = CSVSkipItem
        fields = ['sku', 'name']
        widgets = {
            'sku': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'SKU to skip'}),
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Item name'}),
        }


class RaumplusOrderingRuleForm(forms.ModelForm):
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 5,
            'style': 'font-family: inherit; font-size: 14px; line-height: 1.45;'
        }),
        help_text='Description used in the rule modal.',
    )

    applicable_products = forms.ModelMultipleChoiceField(
        queryset=StockItem.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-control'}),
        help_text='Optional: choose products this rule applies to. Leave blank to apply globally.',
    )

    class Meta:
        model = RaumplusOrderingRule
        fields = [
            'label',
            'default_value',
            'enabled_default',
            'description',
            'applicable_products',
            'is_active',
        ]
        widgets = {
            'label': forms.TextInput(attrs={'class': 'form-control'}),
            'default_value': forms.TextInput(attrs={'class': 'form-control'}),
            'enabled_default': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['applicable_products'].queryset = StockItem.objects.filter(
            Q(category__name__iexact='Sliding Gear') | Q(category_name__iexact='Sliding Gear')
        ).order_by('sku', 'name')

        if self.instance and self.instance.pk:
            self.fields['description'].initial = (self.instance.help_text or self.instance.default_help_text or '-').strip()

    def save(self, commit=True):
        instance = super().save(commit=False)
        description = (self.cleaned_data.get('description') or '').strip() or '-'
        instance.help_text = description
        instance.default_help_text = description

        if commit:
            instance.save()
            self.save_m2m()

        return instance
