from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0004_unique_org_owner_constraint")]
    operations = [migrations.AddField(model_name="apikey", name="scope", field=models.CharField(choices=[("read", "Read"), ("read_write", "Read and write")], default="read_write", max_length=20))]
