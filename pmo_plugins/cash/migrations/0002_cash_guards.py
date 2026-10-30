from django.db import migrations


def install(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        if vendor == 'postgresql':
            cursor.execute("""CREATE FUNCTION cash_funding_account_guard() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Funding account history cannot be deleted';
                  END IF;
                  IF OLD.status = 'active' THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Active funding account is immutable';
                  END IF;
                  RETURN NEW;
                END; $$""")
            cursor.execute('CREATE TRIGGER cash_funding_account_guard BEFORE UPDATE OR DELETE ON cash_fundingaccount FOR EACH ROW EXECUTE FUNCTION cash_funding_account_guard()')
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
            cursor.execute('CREATE TRIGGER cash_party_match_immutable BEFORE UPDATE OR DELETE ON cash_cashpartymatch FOR EACH ROW EXECUTE FUNCTION pmo_prevent_mutation()')
            cursor.execute('CREATE TRIGGER cash_receipt_immutable BEFORE UPDATE OR DELETE ON cash_cashreceipt FOR EACH ROW EXECUTE FUNCTION pmo_prevent_mutation()')
        elif vendor == 'sqlite':
            cursor.execute("""CREATE TRIGGER cash_funding_account_no_delete BEFORE DELETE ON cash_fundingaccount
                BEGIN SELECT RAISE(ABORT, 'Funding account history cannot be deleted'); END""")
            cursor.execute("""CREATE TRIGGER cash_funding_account_active_immutable BEFORE UPDATE ON cash_fundingaccount
                WHEN OLD.status = 'active'
                BEGIN SELECT RAISE(ABORT, 'Active funding account is immutable'); END""")
            cursor.execute("""CREATE TRIGGER cash_transaction_no_delete BEFORE DELETE ON cash_cashtransaction
                BEGIN SELECT RAISE(ABORT, 'Cash transaction history cannot be deleted'); END""")
            cursor.execute("""CREATE TRIGGER cash_transaction_only_draft_editable BEFORE UPDATE ON cash_cashtransaction
                WHEN OLD.status <> 'draft'
                BEGIN SELECT RAISE(ABORT, 'Only draft cash transactions are editable'); END""")
            for table in ('cash_cashpartymatch', 'cash_cashreceipt'):
                for action in ('UPDATE', 'DELETE'):
                    cursor.execute(f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'Cash facts are append-only'); END")


def uninstall(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        if vendor == 'postgresql':
            cursor.execute('DROP TRIGGER IF EXISTS cash_funding_account_guard ON cash_fundingaccount')
            cursor.execute('DROP TRIGGER IF EXISTS cash_transaction_guard ON cash_cashtransaction')
            cursor.execute('DROP TRIGGER IF EXISTS cash_party_match_immutable ON cash_cashpartymatch')
            cursor.execute('DROP TRIGGER IF EXISTS cash_receipt_immutable ON cash_cashreceipt')
            cursor.execute('DROP FUNCTION IF EXISTS cash_funding_account_guard()')
            cursor.execute('DROP FUNCTION IF EXISTS cash_transaction_guard()')
        elif vendor == 'sqlite':
            names = ['cash_funding_account_no_delete', 'cash_funding_account_active_immutable',
                     'cash_transaction_no_delete', 'cash_transaction_only_draft_editable',
                     'cash_cashpartymatch_no_update', 'cash_cashpartymatch_no_delete',
                     'cash_cashreceipt_no_update', 'cash_cashreceipt_no_delete']
            for name in names:
                cursor.execute(f'DROP TRIGGER IF EXISTS {name}')


class Migration(migrations.Migration):
    dependencies = [('cash', '0001_initial')]
    operations = [migrations.RunPython(install, uninstall)]
