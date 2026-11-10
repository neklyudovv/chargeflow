import django.core.validators
import plans.validators
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0006_customer_provider_tokens'),
        ('plans', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='plan',
            name='currency',
            field=models.CharField(default='USD', max_length=3, validators=[plans.validators.validate_currency]),
        ),
        migrations.AlterField(
            model_name='plan',
            name='price',
            field=models.DecimalField(decimal_places=2, max_digits=10, validators=[django.core.validators.MinValueValidator(Decimal('0'))]),
        ),
        migrations.AlterField(
            model_name='plan',
            name='trial_period_days',
            field=models.IntegerField(default=0, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AddConstraint(
            model_name='plan',
            constraint=models.CheckConstraint(condition=models.Q(('price__gte', 0)), name='plan_price_nonnegative'),
        ),
        migrations.AddConstraint(
            model_name='plan',
            constraint=models.CheckConstraint(condition=models.Q(('trial_period_days__gte', 0)), name='plan_trial_nonnegative'),
        ),
    ]
