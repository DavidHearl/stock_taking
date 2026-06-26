# Generated for ActivityLog event_type choices update

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0225_consolidate_cad_number'),
    ]

    operations = [
        migrations.AlterField(
            model_name='activitylog',
            name='event_type',
            field=models.CharField(
                choices=[
                    ('page_action', 'Page Action'),
                    ('job_finished', 'Job Marked as Finished'),
                    ('job_unfinished', 'Job Marked as Unfinished'),
                    ('order_created', 'Order Created'),
                    ('order_deleted', 'Order Deleted'),
                    ('po_created', 'Purchase Order Created'),
                    ('po_deleted', 'Purchase Order Deleted'),
                    ('invoice_created', 'Purchase Invoice Created'),
                    ('invoice_deleted', 'Purchase Invoice Deleted'),
                    ('timesheet_added', 'Timesheet Added'),
                    ('timesheet_deleted', 'Timesheet Deleted'),
                    ('product_created', 'Product Created'),
                    ('product_updated', 'Product Updated'),
                    ('stock_adjusted', 'Stock Adjusted'),
                    ('accessories_generated', 'Accessories Generated'),
                    ('po_split', 'Purchase Order Split'),
                    ('po_status_change', 'PO Status Change'),
                    ('po_updated', 'Purchase Order Updated'),
                    ('error', 'Error'),
                    ('other', 'Other'),
                ],
                db_index=True,
                max_length=50,
            ),
        ),
    ]
