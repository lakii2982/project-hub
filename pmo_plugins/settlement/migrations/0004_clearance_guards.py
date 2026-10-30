from django.db import migrations


def install(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        if vendor == 'postgresql':
            cursor.execute("""CREATE FUNCTION settlement_clearance_guard() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Clearance history cannot be deleted';
                  END IF;
                  IF TG_OP = 'INSERT' THEN
                    IF NOT EXISTS(SELECT 1 FROM settlement_openitem i WHERE i.id = NEW.open_item_id
                        AND i.organization_id = NEW.organization_id)
                    THEN RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Clearance open item must belong to the same organization'; END IF;
                    RETURN NEW;
                  END IF;
                  IF OLD.status = 'reversed' THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Reversed clearance is immutable';
                  END IF;
                  IF NEW.status <> 'reversed' OR NEW.organization_id <> OLD.organization_id
                     OR NEW.open_item_id <> OLD.open_item_id
                     OR NEW.cash_transaction_id <> OLD.cash_transaction_id
                     OR NEW.cash_transaction_ref <> OLD.cash_transaction_ref
                     OR NEW.cleared_cents <> OLD.cleared_cents
                     OR NEW.occurred_on <> OLD.occurred_on OR NEW.version <> OLD.version
                     OR NEW.created_by_id <> OLD.created_by_id OR NEW.created_at <> OLD.created_at
                  THEN RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Clearance may only change status to reversed'; END IF;
                  RETURN NEW;
                END; $$""")
            cursor.execute('CREATE TRIGGER settlement_clearance_guard BEFORE INSERT OR UPDATE OR DELETE ON settlement_clearance FOR EACH ROW EXECUTE FUNCTION settlement_clearance_guard()')
            cursor.execute("""CREATE FUNCTION settlement_clearance_receipt_guard() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Clearance receipt is immutable';
                END; $$""")
            cursor.execute('CREATE TRIGGER settlement_clearance_receipt_immutable BEFORE UPDATE OR DELETE ON settlement_clearancereceipt FOR EACH ROW EXECUTE FUNCTION settlement_clearance_receipt_guard()')
        elif vendor == 'sqlite':
            cursor.execute("""CREATE TRIGGER settlement_clearance_insert_guard BEFORE INSERT ON settlement_clearance
                WHEN NOT EXISTS(SELECT 1 FROM settlement_openitem i WHERE i.id = NEW.open_item_id
                    AND i.organization_id = NEW.organization_id)
                BEGIN SELECT RAISE(ABORT, 'Clearance open item must belong to the same organization'); END""")
            cursor.execute("""CREATE TRIGGER settlement_clearance_no_delete BEFORE DELETE ON settlement_clearance
                BEGIN SELECT RAISE(ABORT, 'Clearance history cannot be deleted'); END""")
            cursor.execute("""CREATE TRIGGER settlement_clearance_status_only BEFORE UPDATE ON settlement_clearance
                WHEN OLD.status = 'reversed' OR NEW.status <> 'reversed'
                    OR NEW.organization_id <> OLD.organization_id OR NEW.open_item_id <> OLD.open_item_id
                    OR NEW.cash_transaction_id <> OLD.cash_transaction_id
                    OR NEW.cash_transaction_ref <> OLD.cash_transaction_ref
                    OR NEW.cleared_cents <> OLD.cleared_cents OR NEW.occurred_on <> OLD.occurred_on
                    OR NEW.version <> OLD.version OR NEW.created_by_id <> OLD.created_by_id
                    OR NEW.created_at <> OLD.created_at
                BEGIN SELECT RAISE(ABORT, 'Clearance may only change status to reversed'); END""")
            for action in ('UPDATE', 'DELETE'):
                cursor.execute(f"CREATE TRIGGER settlement_clearance_receipt_no_{action.lower()} BEFORE {action} ON settlement_clearancereceipt BEGIN SELECT RAISE(ABORT, 'Clearance receipt is immutable'); END")


def uninstall(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        if vendor == 'postgresql':
            cursor.execute('DROP TRIGGER IF EXISTS settlement_clearance_guard ON settlement_clearance')
            cursor.execute('DROP TRIGGER IF EXISTS settlement_clearance_receipt_immutable ON settlement_clearancereceipt')
            cursor.execute('DROP FUNCTION IF EXISTS settlement_clearance_guard()')
            cursor.execute('DROP FUNCTION IF EXISTS settlement_clearance_receipt_guard()')
        elif vendor == 'sqlite':
            for name in ('settlement_clearance_insert_guard', 'settlement_clearance_no_delete',
                         'settlement_clearance_status_only', 'settlement_clearance_receipt_no_update',
                         'settlement_clearance_receipt_no_delete'):
                cursor.execute(f'DROP TRIGGER IF EXISTS {name}')


class Migration(migrations.Migration):
    dependencies = [('settlement', '0003_clearance_clearancereceipt')]
    operations = [migrations.RunPython(install, uninstall)]
