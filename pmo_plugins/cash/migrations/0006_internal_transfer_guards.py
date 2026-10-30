"""PH-245 internal transfer facts: append-only pairing, reversal and receipts with dual triggers."""
import uuid

import django.db.models.deletion
from django.db import migrations, models


def install(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        if vendor == 'postgresql':
            cursor.execute("""CREATE FUNCTION cash_internal_transfer_guard() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Internal transfer history cannot be deleted';
                  END IF;
                  IF TG_OP = 'INSERT' THEN
                    IF NOT EXISTS(SELECT 1 FROM cash_cashtransaction o WHERE o.id = NEW.out_cash_transaction_id
                        AND o.organization_id = NEW.organization_id AND o.direction = 'out')
                    THEN RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Out leg must be a posted outgoing cash fact of the same organization'; END IF;
                    IF NOT EXISTS(SELECT 1 FROM cash_cashtransaction i WHERE i.id = NEW.in_cash_transaction_id
                        AND i.organization_id = NEW.organization_id AND i.direction = 'in')
                    THEN RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'In leg must be a posted incoming cash fact of the same organization'; END IF;
                    RETURN NEW;
                  END IF;
                  IF OLD.status <> 'active' OR NEW.status <> 'reversed'
                     OR NEW.organization_id <> OLD.organization_id
                     OR NEW.out_cash_transaction_id <> OLD.out_cash_transaction_id
                     OR NEW.in_cash_transaction_id <> OLD.in_cash_transaction_id
                     OR NEW.out_cash_ref <> OLD.out_cash_ref
                     OR NEW.in_cash_ref <> OLD.in_cash_ref
                     OR NEW.transfer_date <> OLD.transfer_date
                     OR NEW.gross_cents <> OLD.gross_cents
                     OR NEW.out_transaction_date <> OLD.out_transaction_date
                     OR NEW.in_transaction_date <> OLD.in_transaction_date
                     OR NEW.version <> OLD.version
                     OR NEW.created_by_id <> OLD.created_by_id OR NEW.created_at <> OLD.created_at
                  THEN RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Internal transfer may only change status to reversed'; END IF;
                  RETURN NEW;
                END; $$""")
            cursor.execute('CREATE TRIGGER cash_internal_transfer_guard BEFORE INSERT OR UPDATE OR DELETE ON cash_internaltransfer FOR EACH ROW EXECUTE FUNCTION cash_internal_transfer_guard()')
            cursor.execute("""CREATE FUNCTION cash_internal_transfer_reversal_guard() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Internal transfer reversal cannot be deleted';
                  END IF;
                  IF TG_OP = 'INSERT' THEN
                    IF NOT EXISTS(SELECT 1 FROM cash_internaltransfer t WHERE t.id = NEW.internal_transfer_id
                        AND t.organization_id = NEW.organization_id)
                    THEN RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Reversal transfer must belong to the same organization'; END IF;
                    RETURN NEW;
                  END IF;
                  RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Internal transfer reversal is immutable';
                END; $$""")
            cursor.execute('CREATE TRIGGER cash_internal_transfer_reversal_guard BEFORE INSERT OR UPDATE OR DELETE ON cash_internaltransferreversal FOR EACH ROW EXECUTE FUNCTION cash_internal_transfer_reversal_guard()')
            cursor.execute("""CREATE FUNCTION cash_internal_transfer_receipt_guard() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Internal transfer receipt is immutable';
                END; $$""")
            cursor.execute('CREATE TRIGGER cash_internal_transfer_receipt_immutable BEFORE UPDATE OR DELETE ON cash_internaltransferreceipt FOR EACH ROW EXECUTE FUNCTION cash_internal_transfer_receipt_guard()')
        elif vendor == 'sqlite':
            cursor.execute("""CREATE TRIGGER cash_internal_transfer_insert_guard BEFORE INSERT ON cash_internaltransfer
                WHEN NOT EXISTS(SELECT 1 FROM cash_cashtransaction o WHERE o.id = NEW.out_cash_transaction_id
                    AND o.organization_id = NEW.organization_id AND o.direction = 'out')
                  OR NOT EXISTS(SELECT 1 FROM cash_cashtransaction i WHERE i.id = NEW.in_cash_transaction_id
                    AND i.organization_id = NEW.organization_id AND i.direction = 'in')
                BEGIN SELECT RAISE(ABORT, 'Internal transfer legs must be posted out/in cash facts of the same organization'); END""")
            cursor.execute("""CREATE TRIGGER cash_internal_transfer_no_delete BEFORE DELETE ON cash_internaltransfer
                BEGIN SELECT RAISE(ABORT, 'Internal transfer history cannot be deleted'); END""")
            cursor.execute("""CREATE TRIGGER cash_internal_transfer_status_only BEFORE UPDATE ON cash_internaltransfer
                WHEN OLD.status <> 'active' OR NEW.status <> 'reversed'
                    OR NEW.organization_id <> OLD.organization_id
                    OR NEW.out_cash_transaction_id <> OLD.out_cash_transaction_id
                    OR NEW.in_cash_transaction_id <> OLD.in_cash_transaction_id
                    OR NEW.out_cash_ref <> OLD.out_cash_ref OR NEW.in_cash_ref <> OLD.in_cash_ref
                    OR NEW.transfer_date <> OLD.transfer_date
                    OR NEW.gross_cents <> OLD.gross_cents
                    OR NEW.out_transaction_date <> OLD.out_transaction_date
                    OR NEW.in_transaction_date <> OLD.in_transaction_date
                    OR NEW.version <> OLD.version OR NEW.created_by_id <> OLD.created_by_id
                    OR NEW.created_at <> OLD.created_at
                BEGIN SELECT RAISE(ABORT, 'Internal transfer may only change status to reversed'); END""")
            cursor.execute("""CREATE TRIGGER cash_internal_transfer_reversal_insert_guard BEFORE INSERT ON cash_internaltransferreversal
                WHEN NOT EXISTS(SELECT 1 FROM cash_internaltransfer t WHERE t.id = NEW.internal_transfer_id
                    AND t.organization_id = NEW.organization_id)
                BEGIN SELECT RAISE(ABORT, 'Reversal transfer must belong to the same organization'); END""")
            for action in ('UPDATE', 'DELETE'):
                cursor.execute(f"CREATE TRIGGER cash_internal_transfer_reversal_no_{action.lower()} BEFORE {action} ON cash_internaltransferreversal BEGIN SELECT RAISE(ABORT, 'Internal transfer reversal is immutable'); END")
            for action in ('UPDATE', 'DELETE'):
                cursor.execute(f"CREATE TRIGGER cash_internal_transfer_receipt_no_{action.lower()} BEFORE {action} ON cash_internaltransferreceipt BEGIN SELECT RAISE(ABORT, 'Internal transfer receipt is immutable'); END")


def uninstall(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        if vendor == 'postgresql':
            cursor.execute('DROP TRIGGER IF EXISTS cash_internal_transfer_guard ON cash_internaltransfer')
            cursor.execute('DROP TRIGGER IF EXISTS cash_internal_transfer_reversal_guard ON cash_internaltransferreversal')
            cursor.execute('DROP TRIGGER IF EXISTS cash_internal_transfer_receipt_immutable ON cash_internaltransferreceipt')
            cursor.execute('DROP FUNCTION IF EXISTS cash_internal_transfer_guard()')
            cursor.execute('DROP FUNCTION IF EXISTS cash_internal_transfer_reversal_guard()')
            cursor.execute('DROP FUNCTION IF EXISTS cash_internal_transfer_receipt_guard()')
        elif vendor == 'sqlite':
            for name in ('cash_internal_transfer_insert_guard', 'cash_internal_transfer_no_delete',
                         'cash_internal_transfer_status_only',
                         'cash_internal_transfer_reversal_insert_guard',
                         'cash_internal_transfer_reversal_no_update',
                         'cash_internal_transfer_reversal_no_delete',
                         'cash_internal_transfer_receipt_no_update',
                         'cash_internal_transfer_receipt_no_delete'):
                cursor.execute(f'DROP TRIGGER IF EXISTS {name}')


class Migration(migrations.Migration):
    dependencies = [('cash', '0005_cash_classification_guards')]
    operations = [
        migrations.CreateModel(
            name='InternalTransfer',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True,
                                        serialize=False)),
                ('out_cash_ref', models.JSONField()),
                ('in_cash_ref', models.JSONField()),
                ('transfer_date', models.DateField()),
                ('gross_cents', models.PositiveBigIntegerField()),
                ('out_transaction_date', models.DateField()),
                ('in_transaction_date', models.DateField()),
                ('status', models.CharField(default='active', max_length=16)),
                ('version', models.PositiveIntegerField(default=1)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                                 related_name='+', to='core.membership')),
                ('in_cash_transaction', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT, related_name='in_transfers',
                    to='cash.cashtransaction')),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                                   related_name='internal_transfers',
                                                   to='core.organization')),
                ('out_cash_transaction', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT, related_name='out_transfers',
                    to='cash.cashtransaction')),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(fields=['out_cash_transaction'],
                                            name='cash_internal_transfer_out_unique'),
                    models.UniqueConstraint(fields=['in_cash_transaction'],
                                            name='cash_internal_transfer_in_unique'),
                    models.CheckConstraint(condition=models.Q(gross_cents__gt=0, gross_cents__lte=10 ** 12),
                                           name='cash_internal_transfer_gross_bound'),
                    models.CheckConstraint(condition=models.Q(status__in=[
                        'draft', 'active', 'posted', 'reversed', 'superseded']),
                        name='cash_internal_transfer_status'),
                ],
            },
        ),
        migrations.CreateModel(
            name='InternalTransferReversal',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True,
                                        serialize=False)),
                ('internal_transfer_ref', models.JSONField()),
                ('out_classification_reversal_ref', models.JSONField()),
                ('in_classification_reversal_ref', models.JSONField()),
                ('occurred_on', models.DateField()),
                ('posting_period', models.CharField(max_length=7)),
                ('status', models.CharField(default='posted', max_length=16)),
                ('version', models.PositiveIntegerField(default=1)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                                 related_name='+', to='core.membership')),
                ('internal_transfer', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT, related_name='reversals',
                    to='cash.internaltransfer', unique=True)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                                   related_name='+', to='core.organization')),
            ],
            options={
                'constraints': [
                    models.CheckConstraint(condition=models.Q(status='posted'),
                                           name='cash_internal_transfer_reversal_status'),
                ],
            },
        ),
        migrations.CreateModel(
            name='InternalTransferReceipt',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True,
                                        serialize=False)),
                ('request_token', models.UUIDField()),
                ('action', models.CharField(max_length=40)),
                ('input_digest', models.CharField(max_length=64)),
                ('result', models.JSONField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                                 related_name='+', to='core.membership')),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                                   related_name='+', to='core.organization')),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(fields=['organization', 'request_token'],
                                            name='cash_internal_transfer_receipt_token_unique'),
                ],
            },
        ),
        migrations.RunPython(install, uninstall),
    ]
