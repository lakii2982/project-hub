import django.db.models.deletion
import uuid
from django.db import migrations, models


def install(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        if vendor == 'postgresql':
            cursor.execute("""CREATE FUNCTION cash_classification_guard() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Cash classification history cannot be deleted';
                  END IF;
                  IF TG_OP = 'INSERT' THEN
                    IF NOT EXISTS(SELECT 1 FROM cash_cashtransaction t WHERE t.id = NEW.cash_transaction_id
                        AND t.organization_id = NEW.organization_id)
                    THEN RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Cash classification transaction must belong to the same organization'; END IF;
                    IF NEW.status <> 'active' THEN
                      RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Cash classification must start active';
                    END IF;
                    RETURN NEW;
                  END IF;
                  IF OLD.status = 'reversed' THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Reversed cash classification is immutable';
                  END IF;
                  IF NEW.status <> 'reversed' OR NEW.organization_id <> OLD.organization_id
                     OR NEW.cash_transaction_id <> OLD.cash_transaction_id
                     OR NEW.classification <> OLD.classification OR NEW.version <> OLD.version
                     OR NEW.evidence <> OLD.evidence OR NEW.created_by_id <> OLD.created_by_id
                     OR NEW.created_at <> OLD.created_at
                  THEN RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Cash classification may only change status to reversed'; END IF;
                  RETURN NEW;
                END; $$""")
            cursor.execute('CREATE TRIGGER cash_classification_guard BEFORE INSERT OR UPDATE OR DELETE ON cash_cashclassification FOR EACH ROW EXECUTE FUNCTION cash_classification_guard()')
            cursor.execute("""CREATE FUNCTION cash_classification_reversal_guard() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Cash classification reversal is immutable';
                END; $$""")
            cursor.execute('CREATE TRIGGER cash_classification_reversal_immutable BEFORE UPDATE OR DELETE ON cash_cashclassificationreversal FOR EACH ROW EXECUTE FUNCTION cash_classification_reversal_guard()')
        elif vendor == 'sqlite':
            cursor.execute("""CREATE TRIGGER cash_classification_insert_guard BEFORE INSERT ON cash_cashclassification
                WHEN NOT EXISTS(SELECT 1 FROM cash_cashtransaction t WHERE t.id = NEW.cash_transaction_id
                    AND t.organization_id = NEW.organization_id)
                BEGIN SELECT RAISE(ABORT, 'Cash classification transaction must belong to the same organization'); END""")
            cursor.execute("""CREATE TRIGGER cash_classification_no_delete BEFORE DELETE ON cash_cashclassification
                BEGIN SELECT RAISE(ABORT, 'Cash classification history cannot be deleted'); END""")
            cursor.execute("""CREATE TRIGGER cash_classification_status_only BEFORE UPDATE ON cash_cashclassification
                WHEN OLD.status = 'reversed' OR NEW.status <> 'reversed'
                    OR NEW.organization_id <> OLD.organization_id
                    OR NEW.cash_transaction_id <> OLD.cash_transaction_id
                    OR NEW.classification <> OLD.classification OR NEW.version <> OLD.version
                    OR NEW.evidence <> OLD.evidence OR NEW.created_by_id <> OLD.created_by_id
                    OR NEW.created_at <> OLD.created_at
                BEGIN SELECT RAISE(ABORT, 'Cash classification may only change status to reversed'); END""")
            for action in ('UPDATE', 'DELETE'):
                cursor.execute(f"CREATE TRIGGER cash_classification_reversal_no_{action.lower()} BEFORE {action} ON cash_cashclassificationreversal BEGIN SELECT RAISE(ABORT, 'Cash classification reversal is immutable'); END")


def uninstall(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        if vendor == 'postgresql':
            cursor.execute('DROP TRIGGER IF EXISTS cash_classification_guard ON cash_cashclassification')
            cursor.execute('DROP TRIGGER IF EXISTS cash_classification_reversal_immutable ON cash_cashclassificationreversal')
            cursor.execute('DROP FUNCTION IF EXISTS cash_classification_guard()')
            cursor.execute('DROP FUNCTION IF EXISTS cash_classification_reversal_guard()')
        elif vendor == 'sqlite':
            for name in ('cash_classification_insert_guard', 'cash_classification_no_delete',
                         'cash_classification_status_only',
                         'cash_classification_reversal_no_update',
                         'cash_classification_reversal_no_delete'):
                cursor.execute(f'DROP TRIGGER IF EXISTS {name}')


class Migration(migrations.Migration):

    dependencies = [
        ('cash', '0004_refund_guards'),
        ('core', '0008_alter_accountingevent_labor_origin_ref'),
    ]

    operations = [
        migrations.CreateModel(
            name='CashClassification',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('classification', models.CharField(max_length=16)),
                ('status', models.CharField(default='active', max_length=16)),
                ('version', models.PositiveIntegerField(default=1)),
                ('evidence', models.JSONField(default=list)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('cash_transaction', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='classifications', to='cash.cashtransaction')),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='core.membership')),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='cash_classifications', to='core.organization')),
            ],
        ),
        migrations.CreateModel(
            name='CashClassificationReversal',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('status', models.CharField(default='posted', max_length=16)),
                ('reason', models.TextField()),
                ('evidence', models.JSONField(default=list)),
                ('occurred_on', models.DateField()),
                ('posting_period', models.CharField(max_length=7)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('cash_transaction', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='classification_reversals', to='cash.cashtransaction')),
                ('classification', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='reversals', to='cash.cashclassification')),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='core.membership')),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='core.organization')),
            ],
        ),
        migrations.AddConstraint(
            model_name='cashclassification',
            constraint=models.CheckConstraint(condition=models.Q(('classification__in', ['receipt', 'payment', 'advance', 'tax', 'transfer'])), name='cash_classification_kind'),
        ),
        migrations.AddConstraint(
            model_name='cashclassification',
            constraint=models.CheckConstraint(condition=models.Q(('status__in', ['active', 'reversed'])), name='cash_classification_status'),
        ),
        migrations.AddConstraint(
            model_name='cashclassification',
            constraint=models.UniqueConstraint(condition=models.Q(('status', 'active')), fields=('organization', 'cash_transaction'), name='cash_classification_single_active'),
        ),
        migrations.AddConstraint(
            model_name='cashclassificationreversal',
            constraint=models.UniqueConstraint(fields=('classification',), name='cash_classification_reversal_unique'),
        ),
        migrations.AddConstraint(
            model_name='cashclassificationreversal',
            constraint=models.CheckConstraint(condition=models.Q(('status', 'posted')), name='cash_classification_reversal_status'),
        ),
        migrations.RunPython(install, uninstall),
    ]
