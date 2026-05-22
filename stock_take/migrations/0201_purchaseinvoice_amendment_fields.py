from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0200_invoice_amendment_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='purchaseinvoice',
            name='parent_invoice',
            field=models.ForeignKey(
                blank=True,
                help_text='Parent invoice this is an amendment of',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='amendments',
                to='stock_take.purchaseinvoice',
            ),
        ),
        migrations.AddField(
            model_name='purchaseinvoice',
            name='amendment_reason',
            field=models.TextField(blank=True, help_text='Reason / description for this amendment invoice'),
        ),
    ]
