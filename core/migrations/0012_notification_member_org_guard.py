"""PH-340 database guard: Notification member and organization must match."""

from django.db import migrations


def install_guard(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        if vendor == 'postgresql':
            cursor.execute("""
                CREATE OR REPLACE FUNCTION core_notification_member_org_guard() RETURNS trigger AS $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM core_membership
                        WHERE id = NEW.member_id AND organization_id = NEW.organization_id
                    ) THEN
                        RAISE EXCEPTION 'Notification member and organization must match';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql
            """)
            cursor.execute("""
                CREATE TRIGGER core_notification_member_org_guard
                BEFORE INSERT OR UPDATE OF organization_id, member_id ON core_notification
                FOR EACH ROW EXECUTE FUNCTION core_notification_member_org_guard()
            """)
        elif vendor == 'sqlite':
            for action in ('INSERT', 'UPDATE OF organization_id, member_id'):
                cursor.execute(f"""
                    CREATE TRIGGER core_notification_member_org_{'insert' if action == 'INSERT' else 'update'}
                    BEFORE {action} ON core_notification
                    WHEN NOT EXISTS (
                        SELECT 1 FROM core_membership
                        WHERE id = NEW.member_id AND organization_id = NEW.organization_id
                    )
                    BEGIN SELECT RAISE(ABORT, 'Notification member and organization must match'); END
                """)
        else:
            raise RuntimeError('Unsupported database for Notification organization guard')


def remove_guard(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        if vendor == 'postgresql':
            cursor.execute('DROP TRIGGER IF EXISTS core_notification_member_org_guard ON core_notification')
            cursor.execute('DROP FUNCTION IF EXISTS core_notification_member_org_guard()')
        elif vendor == 'sqlite':
            cursor.execute('DROP TRIGGER IF EXISTS core_notification_member_org_insert')
            cursor.execute('DROP TRIGGER IF EXISTS core_notification_member_org_update')


class Migration(migrations.Migration):
    dependencies = [('core', '0011_notification')]
    operations = [migrations.RunPython(install_guard, remove_guard)]
