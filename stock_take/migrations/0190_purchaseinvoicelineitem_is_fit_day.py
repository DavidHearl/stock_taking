from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0189_add_line_date_to_invoice_line'),
    ]

    operations = [
        migrations.AddField(
            model_name='purchaseinvoicelineitem',
            name='is_fit_day',
            field=models.BooleanField(default=False, help_text='Mark this line as a fit/installation day so it generates a timesheet entry'),
        ),
    ]
