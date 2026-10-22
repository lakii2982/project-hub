from django.db import migrations

TABLES = ["core_ledgerentry", "core_auditevent"]

def install(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        if vendor == "postgresql":
            cursor.execute("""CREATE FUNCTION pmo_prevent_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'Project ledger and audit are append-only'; END; $$""")
            for table in TABLES:
                cursor.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION pmo_prevent_mutation()")
        elif vendor == "sqlite":
            for table in TABLES:
                for action in ["UPDATE", "DELETE"]:
                    cursor.execute(f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'Project ledger and audit are append-only'); END")

def uninstall(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        if schema_editor.connection.vendor == "postgresql":
            for table in TABLES: cursor.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
            cursor.execute("DROP FUNCTION IF EXISTS pmo_prevent_mutation()")
        elif schema_editor.connection.vendor == "sqlite":
            for table in TABLES:
                for action in ["update", "delete"]: cursor.execute(f"DROP TRIGGER IF EXISTS {table}_no_{action}")

class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]
    operations = [migrations.RunPython(install, uninstall)]
