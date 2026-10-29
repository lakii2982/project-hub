from django.db import migrations


TABLES = ['core_accountingevent', 'core_ledgerposting', 'core_sourceconsumption']


def install(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        for table in TABLES:
            if vendor == 'postgresql':
                cursor.execute(f'CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION pmo_prevent_mutation()')
            elif vendor == 'sqlite':
                for action in ['UPDATE', 'DELETE']:
                    cursor.execute(f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'Monthly labor ledger is append-only'); END")


def uninstall(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        for table in TABLES:
            if schema_editor.connection.vendor == 'postgresql':
                cursor.execute(f'DROP TRIGGER IF EXISTS {table}_immutable ON {table}')
            elif schema_editor.connection.vendor == 'sqlite':
                for action in ['update', 'delete']:
                    cursor.execute(f'DROP TRIGGER IF EXISTS {table}_no_{action}')


class Migration(migrations.Migration):
    dependencies = [('core', '0006_sourceconsumption_timesheet_accounting_mode_and_more')]
    operations = [migrations.RunPython(install, uninstall)]
