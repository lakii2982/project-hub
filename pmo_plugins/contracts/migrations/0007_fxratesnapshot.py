"""PH-321: contracts-owned append-only FX rate snapshot table.

Creates the managed FxRateSnapshot model state and table, then installs
vendor-aware UPDATE/DELETE guards. No core migration is touched.
"""

import django.db.models.deletion
import uuid
from django.db import migrations, models


def install_guards(apps, schema_editor):
    table = 'contracts_fxratesnapshot'
    with schema_editor.connection.cursor() as cursor:
        if schema_editor.connection.vendor == 'postgresql':
            cursor.execute("""
                CREATE OR REPLACE FUNCTION contracts_fx_rate_append_only() RETURNS trigger
                LANGUAGE plpgsql AS $$
                BEGIN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'FX rate history is append-only';
                END;
                $$""")
            cursor.execute(f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
                           f"FOR EACH ROW EXECUTE FUNCTION contracts_fx_rate_append_only()")
        else:
            cursor.execute(f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} "
                           f"BEGIN SELECT RAISE(ABORT, 'FX rate history is append-only'); END")
            cursor.execute(f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} "
                           f"BEGIN SELECT RAISE(ABORT, 'FX rate history is append-only'); END")


def drop_guards(apps, schema_editor):
    table = 'contracts_fxratesnapshot'
    with schema_editor.connection.cursor() as cursor:
        if schema_editor.connection.vendor == 'postgresql':
            cursor.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
            cursor.execute("DROP FUNCTION IF EXISTS contracts_fx_rate_append_only()")
        else:
            cursor.execute(f"DROP TRIGGER IF EXISTS {table}_no_update")
            cursor.execute(f"DROP TRIGGER IF EXISTS {table}_no_delete")



class Migration(migrations.Migration):

    dependencies = [
        ('contracts', '0006_ph324_fx_rate_ref_and_cny_backfill'),
        ('core', '0010_membership_version'),
    ]

    operations = [
        migrations.CreateModel(
            name='FxRateSnapshot',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('base_currency', models.CharField(default='CNY', max_length=3)),
                ('quote_currency', models.CharField(max_length=3)),
                ('effective_on', models.DateField()),
                ('rate', models.DecimalField(decimal_places=8, max_digits=18)),
                ('source', models.CharField(default='manual', max_length=16)),
                ('reference', models.CharField(blank=True, default='', max_length=240)),
                ('version', models.PositiveIntegerField(default=1)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('algorithm_version', models.CharField(default='fx_rate_snapshot_v1', max_length=64)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='core.membership')),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='fx_rate_snapshots', to='core.organization')),
            ],
            options={
                'db_table': 'contracts_fxratesnapshot',
                'ordering': ['-effective_on', 'quote_currency', '-version'],
                'constraints': [models.UniqueConstraint(fields=('organization', 'base_currency', 'quote_currency', 'effective_on', 'version'), name='contracts_fx_rate_version_unique'), models.CheckConstraint(condition=models.Q(('rate__gt', 0)), name='contracts_fx_rate_positive'), models.CheckConstraint(condition=models.Q(('base_currency', models.F('quote_currency')), _negated=True), name='contracts_fx_rate_pair_distinct'), models.CheckConstraint(condition=models.Q(('source__in', ['manual', 'bank', 'provider'])), name='contracts_fx_rate_source'), models.CheckConstraint(condition=models.Q(('base_currency__regex', '^[A-Z]{3}$'), ('quote_currency__regex', '^[A-Z]{3}$')), name='contracts_fx_rate_currency_code')],
            },
        ),
        migrations.RunPython(install_guards, drop_guards),
    ]
