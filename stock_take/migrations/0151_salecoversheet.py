from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("stock_take", "0150_fitter_upload_staging"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SaleCoverSheet",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("prepared_by", models.CharField(blank=True, max_length=120)),
                ("customer_on_site_name", models.CharField(blank=True, max_length=255)),
                ("customer_on_site_phone", models.CharField(blank=True, max_length=100)),
                ("installation_address", models.TextField(blank=True)),
                ("survey_date", models.DateField(blank=True, null=True)),
                ("fit_date", models.DateField(blank=True, null=True)),
                ("products_scope", models.TextField(blank=True, help_text="Short scope of works / products included")),
                ("measurements_notes", models.TextField(blank=True)),
                ("access_notes", models.TextField(blank=True)),
                ("health_safety_notes", models.TextField(blank=True)),
                ("special_instructions", models.TextField(blank=True)),
                ("is_final", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "sale",
                    models.OneToOneField(
                        help_text="Sale this coversheet belongs to",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="cover_sheet",
                        to="stock_take.anthillsale",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="updated_sale_coversheets",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Sale Coversheet",
                "verbose_name_plural": "Sale Coversheets",
                "ordering": ["-updated_at"],
            },
        ),
    ]
