"""PH-264 canonical Membership optimistic version."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0009_relax_ledger_posting_scope')]
    operations = [
        migrations.AddField(
            model_name='membership',
            name='version',
            field=models.PositiveIntegerField(default=1),
        ),
    ]
