from django.db import migrations, models


def install(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        if vendor == 'postgresql':
            cursor.execute('DROP TRIGGER IF EXISTS cash_transaction_guard ON cash_cashtransaction')
            cursor.execute('DROP FUNCTION IF EXISTS cash_transaction_guard()')
            cursor.execute("""CREATE FUNCTION cash_transaction_posting_guard() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Cash transaction history cannot be deleted';
                  END IF;
                  IF OLD.status = 'posted' THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Posted cash transaction is immutable';
                  END IF;
                  IF NEW.status NOT IN ('draft', 'posted') THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Cash transaction status is invalid';
                  END IF;
                  IF NEW.status = 'posted' AND (NEW.gross_cents <> OLD.gross_cents
                      OR NEW.transaction_date <> OLD.transaction_date OR NEW.direction <> OLD.direction
                      OR NEW.funding_account_id <> OLD.funding_account_id
                      OR NEW.external_source_key <> OLD.external_source_key
                      OR NEW.payee <> OLD.payee OR NEW.evidence <> OLD.evidence
                      OR NEW.counterparty_id IS DISTINCT FROM OLD.counterparty_id
                      OR NEW.created_by_id <> OLD.created_by_id OR NEW.created_at <> OLD.created_at) THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Posting may only change status and version';
                  END IF;
                  RETURN NEW;
                END; $$""")
            cursor.execute('CREATE TRIGGER cash_transaction_guard BEFORE UPDATE OR DELETE ON cash_cashtransaction FOR EACH ROW EXECUTE FUNCTION cash_transaction_posting_guard()')
        elif vendor == 'sqlite':
            cursor.execute('DROP TRIGGER IF EXISTS cash_transaction_only_draft_editable')
            cursor.execute('DROP TRIGGER IF EXISTS cash_transaction_no_delete')
            cursor.execute("""CREATE TRIGGER cash_transaction_no_delete BEFORE DELETE ON cash_cashtransaction
                BEGIN SELECT RAISE(ABORT, 'Cash transaction history cannot be deleted'); END""")
            cursor.execute("""CREATE TRIGGER cash_transaction_posting_guard BEFORE UPDATE ON cash_cashtransaction
                WHEN OLD.status = 'posted' OR NEW.status NOT IN ('draft', 'posted') OR (NEW.status = 'posted' AND (
                    NEW.gross_cents <> OLD.gross_cents OR NEW.transaction_date <> OLD.transaction_date
                    OR NEW.direction <> OLD.direction OR NEW.funding_account_id <> OLD.funding_account_id
                    OR NEW.external_source_key <> OLD.external_source_key OR NEW.payee <> OLD.payee
                    OR NEW.evidence <> OLD.evidence OR coalesce(NEW.counterparty_id, '') <> coalesce(OLD.counterparty_id, '')))
                BEGIN SELECT RAISE(ABORT, 'Posted cash transaction is immutable or posting changed more than status/version'); END""")


def uninstall(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        if vendor == 'postgresql':
            cursor.execute('DROP TRIGGER IF EXISTS cash_transaction_guard ON cash_cashtransaction')
            cursor.execute('DROP FUNCTION IF EXISTS cash_transaction_posting_guard()')
            cursor.execute("""CREATE FUNCTION cash_transaction_guard() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Cash transaction history cannot be deleted';
                  END IF;
                  IF OLD.status <> 'draft' THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Only draft cash transactions are editable';
                  END IF;
                  RETURN NEW;
                END; $$""")
            cursor.execute('CREATE TRIGGER cash_transaction_guard BEFORE UPDATE OR DELETE ON cash_cashtransaction FOR EACH ROW EXECUTE FUNCTION cash_transaction_guard()')
        elif vendor == 'sqlite':
            cursor.execute('DROP TRIGGER IF EXISTS cash_transaction_posting_guard')
            cursor.execute('DROP TRIGGER IF EXISTS cash_transaction_no_delete')
            cursor.execute("""CREATE TRIGGER cash_transaction_no_delete BEFORE DELETE ON cash_cashtransaction
                BEGIN SELECT RAISE(ABORT, 'Cash transaction history cannot be deleted'); END""")
            cursor.execute("""CREATE TRIGGER cash_transaction_only_draft_editable BEFORE UPDATE ON cash_cashtransaction
                WHEN OLD.status <> 'draft'
                BEGIN SELECT RAISE(ABORT, 'Only draft cash transactions are editable'); END""")


class Migration(migrations.Migration):
    dependencies = [('cash', '0002_cash_guards'), ('core', '0008_alter_accountingevent_labor_origin_ref')]
    operations = [
        migrations.RemoveConstraint(model_name='cashtransaction', name='cash_transaction_draft_status'),
        migrations.AddConstraint(model_name='cashtransaction',
            constraint=models.CheckConstraint(condition=models.Q(('status__in', ['draft', 'posted'])), name='cash_transaction_status')),
        migrations.RunPython(install, uninstall),
    ]
