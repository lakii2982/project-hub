from django.db import migrations


FULL = ['delivery_customerconfirmationevidence', 'delivery_acceptancereversal', 'delivery_deliveryreceipt']


def install(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    fields = [f.column for f in apps.get_model('delivery', 'Acceptance')._meta.fields if f.name not in {'status', 'version'}]
    with schema_editor.connection.cursor() as cursor:
        for table in FULL:
            if vendor == 'postgresql':
                cursor.execute(f'CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION pmo_prevent_mutation()')
            elif vendor == 'sqlite':
                for action in ['UPDATE', 'DELETE']:
                    cursor.execute(f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'Customer evidence is immutable'); END")
        if vendor == 'postgresql':
            changed = ' OR '.join(f'OLD.{f} IS DISTINCT FROM NEW.{f}' for f in fields)
            cursor.execute(f"""CREATE FUNCTION delivery_acceptance_protect() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF OLD.status IN ('confirmed','reversed') AND (({changed}) OR NOT
                  ((NEW.status=OLD.status AND NEW.version=OLD.version) OR
                   (OLD.status='confirmed' AND NEW.status='reversed' AND NEW.version=OLD.version+1)))
                THEN RAISE EXCEPTION 'Confirmed acceptance is immutable'; END IF; RETURN NEW; END; $$""")
            cursor.execute('CREATE TRIGGER delivery_acceptance_protect BEFORE UPDATE ON delivery_acceptance FOR EACH ROW EXECUTE FUNCTION delivery_acceptance_protect()')
            cursor.execute('CREATE TRIGGER delivery_acceptance_no_delete BEFORE DELETE ON delivery_acceptance FOR EACH ROW EXECUTE FUNCTION pmo_prevent_mutation()')
            cursor.execute("""CREATE FUNCTION delivery_coverage_protect() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF (TG_OP <> 'INSERT' AND EXISTS(SELECT 1 FROM delivery_acceptance WHERE id=OLD.acceptance_id AND status IN ('confirmed','reversed')))
                    OR (TG_OP <> 'DELETE' AND EXISTS(SELECT 1 FROM delivery_acceptance WHERE id=NEW.acceptance_id AND status IN ('confirmed','reversed')))
                THEN RAISE EXCEPTION 'Confirmed coverage is immutable'; END IF;
                IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF; END; $$""")
            cursor.execute('CREATE TRIGGER delivery_coverage_protect BEFORE INSERT OR UPDATE OR DELETE ON delivery_acceptancecoverage FOR EACH ROW EXECUTE FUNCTION delivery_coverage_protect()')
        elif vendor == 'sqlite':
            changed = ' OR '.join(f'OLD.{f} IS NOT NEW.{f}' for f in fields)
            cursor.execute(f"""CREATE TRIGGER delivery_acceptance_protect BEFORE UPDATE ON delivery_acceptance
                WHEN OLD.status IN ('confirmed','reversed') AND (({changed}) OR NOT
                 ((NEW.status=OLD.status AND NEW.version=OLD.version) OR
                  (OLD.status='confirmed' AND NEW.status='reversed' AND NEW.version=OLD.version+1)))
                BEGIN SELECT RAISE(ABORT, 'Confirmed acceptance is immutable'); END""")
            cursor.execute("CREATE TRIGGER delivery_acceptance_no_delete BEFORE DELETE ON delivery_acceptance BEGIN SELECT RAISE(ABORT, 'Acceptance history cannot be deleted'); END")
            for action in ['INSERT', 'UPDATE', 'DELETE']:
                checks = []
                if action != 'INSERT':
                    checks.append("EXISTS(SELECT 1 FROM delivery_acceptance WHERE id=OLD.acceptance_id AND status IN ('confirmed','reversed'))")
                if action != 'DELETE':
                    checks.append("EXISTS(SELECT 1 FROM delivery_acceptance WHERE id=NEW.acceptance_id AND status IN ('confirmed','reversed'))")
                cursor.execute(f"CREATE TRIGGER delivery_coverage_no_{action.lower()} BEFORE {action} ON delivery_acceptancecoverage WHEN {' OR '.join(checks)} BEGIN SELECT RAISE(ABORT, 'Confirmed coverage is immutable'); END")


def uninstall(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        for table in FULL:
            if schema_editor.connection.vendor == 'postgresql':
                cursor.execute(f'DROP TRIGGER IF EXISTS {table}_immutable ON {table}')
            elif schema_editor.connection.vendor == 'sqlite':
                for action in ['update', 'delete']:
                    cursor.execute(f'DROP TRIGGER IF EXISTS {table}_no_{action}')
        for name, table in [('delivery_acceptance_protect', 'delivery_acceptance'), ('delivery_acceptance_no_delete', 'delivery_acceptance'), ('delivery_coverage_protect', 'delivery_acceptancecoverage')]:
            if schema_editor.connection.vendor == 'postgresql':
                cursor.execute(f'DROP TRIGGER IF EXISTS {name} ON {table}')
            elif schema_editor.connection.vendor == 'sqlite':
                cursor.execute(f'DROP TRIGGER IF EXISTS {name}')
        if schema_editor.connection.vendor == 'postgresql':
            cursor.execute('DROP FUNCTION IF EXISTS delivery_acceptance_protect()')
            cursor.execute('DROP FUNCTION IF EXISTS delivery_coverage_protect()')
        elif schema_editor.connection.vendor == 'sqlite':
            for action in ['insert', 'update', 'delete']:
                cursor.execute(f'DROP TRIGGER IF EXISTS delivery_coverage_no_{action}')


class Migration(migrations.Migration):
    dependencies = [('delivery', '0001_initial')]
    operations = [migrations.RunPython(install, uninstall)]
