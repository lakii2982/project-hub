"""PH-241 settlement Offset: append-only advance/open-item offset fact with dual triggers."""
import uuid

import django.db.models.deletion
from django.db import migrations, models


def install(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        if vendor == 'postgresql':
            cursor.execute("""CREATE FUNCTION settlement_offset_guard() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Offset history cannot be deleted';
                  END IF;
                  IF TG_OP = 'INSERT' THEN
                    IF NOT EXISTS(SELECT 1 FROM settlement_openitem i WHERE i.id = NEW.open_item_id
                        AND i.organization_id = NEW.organization_id
                        AND i.project_id = NEW.project_id)
                    THEN RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Offset open item must belong to the same organization and project'; END IF;
                    RETURN NEW;
                  END IF;
                  RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Offset is append-only';
                END; $$""")
            cursor.execute('CREATE TRIGGER settlement_offset_guard BEFORE INSERT OR UPDATE OR DELETE ON settlement_offset FOR EACH ROW EXECUTE FUNCTION settlement_offset_guard()')
        elif vendor == 'sqlite':
            cursor.execute("""CREATE TRIGGER settlement_offset_insert_guard BEFORE INSERT ON settlement_offset
                WHEN NOT EXISTS(SELECT 1 FROM settlement_openitem i WHERE i.id = NEW.open_item_id
                    AND i.organization_id = NEW.organization_id
                    AND i.project_id = NEW.project_id)
                BEGIN SELECT RAISE(ABORT, 'Offset open item must belong to the same organization and project'); END""")
            for action in ('UPDATE', 'DELETE'):
                cursor.execute(f"CREATE TRIGGER settlement_offset_no_{action.lower()} BEFORE {action} ON settlement_offset BEGIN SELECT RAISE(ABORT, 'Offset is append-only'); END")


def uninstall(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        if vendor == 'postgresql':
            cursor.execute('DROP TRIGGER IF EXISTS settlement_offset_guard ON settlement_offset')
            cursor.execute('DROP FUNCTION IF EXISTS settlement_offset_guard()')
        elif vendor == 'sqlite':
            for name in ('settlement_offset_insert_guard', 'settlement_offset_no_update',
                         'settlement_offset_no_delete'):
                cursor.execute(f'DROP TRIGGER IF EXISTS {name}')


class Migration(migrations.Migration):
    dependencies = [('settlement', '0004_clearance_guards')]
    operations = [
        migrations.CreateModel(
            name='Offset',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True,
                                        serialize=False)),
                ('advance_cash_transaction_id', models.UUIDField()),
                ('advance_ref', models.JSONField()),
                ('offset_cents', models.PositiveBigIntegerField()),
                ('occurred_on', models.DateField()),
                ('status', models.CharField(default='active', max_length=16)),
                ('version', models.PositiveIntegerField(default=1)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                                 related_name='+', to='core.membership')),
                ('open_item', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                                related_name='offsets',
                                                to='settlement.openitem')),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                                   related_name='offsets', to='core.organization')),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                              related_name='offsets', to='core.project')),
            ],
            options={
                'constraints': [
                    models.CheckConstraint(condition=models.Q(offset_cents__gt=0, offset_cents__lte=10 ** 12),
                                           name='settlement_offset_amount_bound'),
                    models.CheckConstraint(condition=models.Q(status='active'),
                                           name='settlement_offset_status'),
                ],
            },
        ),
        migrations.RunPython(install, uninstall),
    ]
