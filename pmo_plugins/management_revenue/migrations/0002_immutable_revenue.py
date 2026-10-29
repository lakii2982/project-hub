from django.db import migrations


TABLES = ['management_revenue_revenuerecognition', 'management_revenue_revenueadjustment']


def install(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        if schema_editor.connection.vendor == 'postgresql':
            cursor.execute("""CREATE FUNCTION pmo_revenue_posted_guard() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF TG_OP='DELETE' OR OLD.status='posted' THEN RAISE EXCEPTION 'Posted revenue is append-only'; END IF;
                RETURN NEW; END; $$""")
            for table in TABLES:
                cursor.execute(f'CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION pmo_revenue_posted_guard()')
            cursor.execute('CREATE TRIGGER management_revenue_receipt_immutable BEFORE UPDATE OR DELETE ON management_revenue_revenuereceipt FOR EACH ROW EXECUTE FUNCTION pmo_prevent_mutation()')
        elif schema_editor.connection.vendor == 'sqlite':
            for table in TABLES:
                cursor.execute(f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} WHEN OLD.status='posted' BEGIN SELECT RAISE(ABORT, 'Posted revenue is append-only'); END")
                cursor.execute(f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT, 'Revenue history is append-only'); END")
            for action in ['UPDATE', 'DELETE']:
                cursor.execute(f"CREATE TRIGGER management_revenue_receipt_no_{action.lower()} BEFORE {action} ON management_revenue_revenuereceipt BEGIN SELECT RAISE(ABORT, 'Revenue receipt is immutable'); END")


def uninstall(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        if schema_editor.connection.vendor == 'postgresql':
            for table in TABLES:
                cursor.execute(f'DROP TRIGGER IF EXISTS {table}_immutable ON {table}')
            cursor.execute('DROP TRIGGER IF EXISTS management_revenue_receipt_immutable ON management_revenue_revenuereceipt')
            cursor.execute('DROP FUNCTION IF EXISTS pmo_revenue_posted_guard()')
        elif schema_editor.connection.vendor == 'sqlite':
            for table in TABLES:
                for action in ['update', 'delete']:
                    cursor.execute(f'DROP TRIGGER IF EXISTS {table}_no_{action}')
            for action in ['update', 'delete']:
                cursor.execute(f'DROP TRIGGER IF EXISTS management_revenue_receipt_no_{action}')


class Migration(migrations.Migration):
    dependencies = [('management_revenue', '0001_initial')]
    operations = [migrations.RunPython(install, uninstall)]
