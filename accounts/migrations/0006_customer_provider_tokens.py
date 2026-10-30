from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0005_apikey_scope")]

    operations = [
        migrations.AddField(
            model_name="customer",
            name="provider_customer_id",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="customer",
            name="provider_payment_method_id",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
