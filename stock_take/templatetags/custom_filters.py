from django import template

register = template.Library()

@register.filter
def get_item(dictionary, key):
    """Get an item from a dictionary using a variable key"""
    if dictionary:
        return dictionary.get(key)
    return None

@register.filter
def calculate_remaining(accessory):
    """Calculate remaining stock: Stock - QTY - Allocated"""
    if not accessory.stock_item:
        return 0
    stock = accessory.available_quantity
    allocated = accessory.allocated_quantity
    qty = accessory.quantity
    return stock - qty - allocated

@register.filter
def split_options(value, delimiter=','):
    """Split a string by delimiter and return a list"""
    if not value:
        return []
    return [option.strip() for option in value.split(delimiter)]

@register.filter
def sum_accessory_costs(accessories):
    """Calculate total cost of all accessories (cost_price * quantity)"""
    total = 0
    for accessory in accessories:
        total += accessory.cost_price * accessory.quantity
    return f"{total:.2f}"

@register.filter
def sum_expenses(expenses):
    """Calculate total amount of all expenses"""
    total = 0
    for expense in expenses:
        total += float(expense.amount or 0)
    return total


@register.filter
def multiply(value, arg):
    """Multiply two numbers. Usage: {{ cost_price|multiply:quantity }}"""
    try:
        return f"{float(value) * float(arg):.2f}"
    except (ValueError, TypeError):
        return '0.00'
