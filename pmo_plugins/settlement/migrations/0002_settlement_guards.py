from django.db import migrations


def install(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        if vendor == 'postgresql':
            cursor.execute("""CREATE FUNCTION settlement_open_item_guard() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  IF TG_OP = 'INSERT' THEN
                    IF NOT EXISTS(SELECT 1 FROM settlement_settlement s WHERE s.id = NEW.settlement_id
                        AND s.organization_id = NEW.organization_id AND s.project_id = NEW.project_id
                        AND s.status = 'submitted')
                    THEN RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Open items may only be created for a submitted settlement of the same organization and project'; END IF;
                    RETURN NEW;
                  END IF;
                  RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Open items are immutable and cannot be deleted';
                END; $$""")
            cursor.execute('CREATE TRIGGER settlement_open_item_guard BEFORE INSERT OR UPDATE OR DELETE ON settlement_openitem FOR EACH ROW EXECUTE FUNCTION settlement_open_item_guard()')
            cursor.execute("""CREATE FUNCTION settlement_parent_guard() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Settlement history cannot be deleted';
                  END IF;
                  IF OLD.status IN ('active','withdrawn') THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Active or withdrawn settlement is immutable';
                  END IF;
                  IF NEW.status = 'active' THEN
                    IF (SELECT count(*) FROM settlement_openitem i WHERE i.settlement_id = NEW.id) = 0
                       OR (SELECT COALESCE(SUM(i.gross_cents),0) FROM settlement_openitem i WHERE i.settlement_id = NEW.id) <> NEW.gross_cents
                       OR EXISTS(SELECT 1 FROM settlement_openitem i WHERE i.settlement_id = NEW.id
                            AND (i.organization_id <> NEW.organization_id OR i.project_id <> NEW.project_id
                                 OR i.direction <> 'receivable'))
                    THEN RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Settlement partitions must equal the approved gross once'; END IF;
                  END IF;
                  RETURN NEW;
                END; $$""")
            cursor.execute('CREATE TRIGGER settlement_parent_guard BEFORE UPDATE OR DELETE ON settlement_settlement FOR EACH ROW EXECUTE FUNCTION settlement_parent_guard()')
            cursor.execute("""CREATE FUNCTION settlement_receipt_guard() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Settlement receipt is immutable';
                END; $$""")
            cursor.execute('CREATE TRIGGER settlement_receipt_immutable BEFORE UPDATE OR DELETE ON settlement_settlementreceipt FOR EACH ROW EXECUTE FUNCTION settlement_receipt_guard()')
        elif vendor == 'sqlite':
            cursor.execute("""CREATE TRIGGER settlement_open_item_no_update BEFORE UPDATE ON settlement_openitem
                BEGIN SELECT RAISE(ABORT, 'Open items are immutable'); END""")
            cursor.execute("""CREATE TRIGGER settlement_open_item_no_delete BEFORE DELETE ON settlement_openitem
                BEGIN SELECT RAISE(ABORT, 'Open items are immutable'); END""")
            cursor.execute("""CREATE TRIGGER settlement_open_item_insert_guard BEFORE INSERT ON settlement_openitem
                WHEN NOT EXISTS(SELECT 1 FROM settlement_settlement s WHERE s.id = NEW.settlement_id
                    AND s.organization_id = NEW.organization_id AND s.project_id = NEW.project_id
                    AND s.status = 'submitted')
                BEGIN SELECT RAISE(ABORT, 'Open items may only be created for a submitted settlement of the same organization and project'); END""")
            cursor.execute("""CREATE TRIGGER settlement_parent_no_delete BEFORE DELETE ON settlement_settlement
                BEGIN SELECT RAISE(ABORT, 'Settlement history cannot be deleted'); END""")
            cursor.execute("""CREATE TRIGGER settlement_parent_active_immutable BEFORE UPDATE ON settlement_settlement
                WHEN OLD.status IN ('active','withdrawn')
                BEGIN SELECT RAISE(ABORT, 'Active or withdrawn settlement is immutable'); END""")
            cursor.execute("""CREATE TRIGGER settlement_parent_partitions BEFORE UPDATE ON settlement_settlement
                WHEN NEW.status = 'active' AND (
                    (SELECT COUNT(*) FROM settlement_openitem i WHERE i.settlement_id = NEW.id) = 0
                    OR (SELECT COALESCE(SUM(i.gross_cents),0) FROM settlement_openitem i WHERE i.settlement_id = NEW.id) <> NEW.gross_cents
                    OR EXISTS(SELECT 1 FROM settlement_openitem i WHERE i.settlement_id = NEW.id
                        AND (i.organization_id <> NEW.organization_id OR i.project_id <> NEW.project_id
                             OR i.direction <> 'receivable')))
                BEGIN SELECT RAISE(ABORT, 'Settlement partitions must equal the approved gross once'); END""")
            for action in ['UPDATE', 'DELETE']:
                cursor.execute(f"CREATE TRIGGER settlement_receipt_no_{action.lower()} BEFORE {action} ON settlement_settlementreceipt BEGIN SELECT RAISE(ABORT, 'Settlement receipt is immutable'); END")


def uninstall(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        if vendor == 'postgresql':
            cursor.execute('DROP TRIGGER IF EXISTS settlement_open_item_guard ON settlement_openitem')
            cursor.execute('DROP TRIGGER IF EXISTS settlement_parent_guard ON settlement_settlement')
            cursor.execute('DROP TRIGGER IF EXISTS settlement_receipt_immutable ON settlement_settlementreceipt')
            cursor.execute('DROP FUNCTION IF EXISTS settlement_open_item_guard()')
            cursor.execute('DROP FUNCTION IF EXISTS settlement_parent_guard()')
            cursor.execute('DROP FUNCTION IF EXISTS settlement_receipt_guard()')
        elif vendor == 'sqlite':
            for name in ['settlement_open_item_no_update', 'settlement_open_item_no_delete',
                         'settlement_open_item_insert_guard', 'settlement_parent_no_delete',
                         'settlement_parent_active_immutable', 'settlement_parent_partitions',
                         'settlement_receipt_no_update', 'settlement_receipt_no_delete']:
                cursor.execute(f'DROP TRIGGER IF EXISTS {name}')


class Migration(migrations.Migration):
    dependencies = [('settlement', '0001_initial')]
    operations = [migrations.RunPython(install, uninstall)]
