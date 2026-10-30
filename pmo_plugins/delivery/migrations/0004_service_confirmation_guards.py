from django.db import migrations


def install(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    model = apps.get_model('delivery', 'ServiceConfirmation')
    full_fields = [f.column for f in model._meta.fields if f.name not in {'status', 'version'}]
    with schema_editor.connection.cursor() as cursor:
        if vendor == 'postgresql':
            cursor.execute('CREATE TRIGGER delivery_serviceconfirmationreversal_immutable BEFORE UPDATE OR DELETE ON delivery_serviceconfirmationreversal FOR EACH ROW EXECUTE FUNCTION pmo_prevent_mutation()')
            changed = ' OR '.join(f'OLD.{field} IS DISTINCT FROM NEW.{field}' for field in full_fields)
            cursor.execute(f"""CREATE FUNCTION delivery_service_confirmation_protect() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Service confirmation history cannot be deleted'; END IF;
                  IF OLD.status IN ('confirmed','reversed') THEN
                    IF NOT (OLD.status='confirmed' AND NEW.status='reversed' AND NEW.version=OLD.version AND NOT ({changed}))
                    THEN RAISE EXCEPTION 'Confirmed service confirmation is immutable'; END IF;
                  END IF;
                  RETURN NEW; END; $$""")
            cursor.execute('CREATE TRIGGER delivery_service_confirmation_protect BEFORE UPDATE ON delivery_serviceconfirmation FOR EACH ROW EXECUTE FUNCTION delivery_service_confirmation_protect()')
            cursor.execute('CREATE TRIGGER delivery_service_confirmation_no_delete BEFORE DELETE ON delivery_serviceconfirmation FOR EACH ROW EXECUTE FUNCTION pmo_prevent_mutation()')
            cursor.execute("""CREATE FUNCTION delivery_service_month_protect() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  IF TG_OP = 'INSERT' THEN
                    IF EXISTS(SELECT 1 FROM delivery_serviceconfirmation WHERE id=NEW.service_confirmation_id AND status IN ('confirmed','reversed'))
                    THEN RAISE EXCEPTION 'Confirmed service month rows are immutable'; END IF;
                    IF NOT EXISTS(SELECT 1 FROM delivery_serviceconfirmation p WHERE p.id=NEW.service_confirmation_id
                        AND NEW.coverage_start >= p.coverage_start AND NEW.coverage_end <= p.coverage_end)
                    THEN RAISE EXCEPTION 'Service month outside confirmation coverage'; END IF;
                    RETURN NEW;
                  END IF;
                  RAISE EXCEPTION 'Service month rows are immutable and cannot be deleted';
                END; $$""")
            cursor.execute('CREATE TRIGGER delivery_service_month_protect BEFORE INSERT OR UPDATE OR DELETE ON delivery_serviceconfirmationmonth FOR EACH ROW EXECUTE FUNCTION delivery_service_month_protect()')
        elif vendor == 'sqlite':
            for action in ['UPDATE', 'DELETE']:
                cursor.execute(f"CREATE TRIGGER delivery_serviceconfirmationreversal_no_{action.lower()} BEFORE {action} ON delivery_serviceconfirmationreversal BEGIN SELECT RAISE(ABORT, 'Service confirmation reversal is immutable'); END")
            changed = ' OR '.join(f'OLD.{field} IS NOT NEW.{field}' for field in full_fields)
            cursor.execute(f"""CREATE TRIGGER delivery_service_confirmation_protect BEFORE UPDATE ON delivery_serviceconfirmation
                WHEN OLD.status IN ('confirmed','reversed') AND NOT
                 (OLD.status='confirmed' AND NEW.status='reversed' AND NEW.version=OLD.version AND NOT ({changed}))
                BEGIN SELECT RAISE(ABORT, 'Confirmed service confirmation is immutable'); END""")
            cursor.execute("CREATE TRIGGER delivery_service_confirmation_no_delete BEFORE DELETE ON delivery_serviceconfirmation BEGIN SELECT RAISE(ABORT, 'Service confirmation history cannot be deleted'); END")
            cursor.execute("""CREATE TRIGGER delivery_service_month_no_insert BEFORE INSERT ON delivery_serviceconfirmationmonth
                WHEN EXISTS(SELECT 1 FROM delivery_serviceconfirmation WHERE id=NEW.service_confirmation_id AND status IN ('confirmed','reversed'))
                BEGIN SELECT RAISE(ABORT, 'Confirmed service month rows are immutable'); END""")
            cursor.execute("""CREATE TRIGGER delivery_service_month_coverage BEFORE INSERT ON delivery_serviceconfirmationmonth
                WHEN NOT EXISTS(SELECT 1 FROM delivery_serviceconfirmation p WHERE p.id=NEW.service_confirmation_id
                    AND NEW.coverage_start >= p.coverage_start AND NEW.coverage_end <= p.coverage_end)
                BEGIN SELECT RAISE(ABORT, 'Service month outside confirmation coverage'); END""")
            for action in ['UPDATE', 'DELETE']:
                cursor.execute(f"CREATE TRIGGER delivery_service_month_no_{action.lower()} BEFORE {action} ON delivery_serviceconfirmationmonth BEGIN SELECT RAISE(ABORT, 'Service month rows are immutable'); END")


def uninstall(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        if vendor == 'postgresql':
            cursor.execute('DROP TRIGGER IF EXISTS delivery_serviceconfirmationreversal_immutable ON delivery_serviceconfirmationreversal')
            cursor.execute('DROP TRIGGER IF EXISTS delivery_service_confirmation_protect ON delivery_serviceconfirmation')
            cursor.execute('DROP TRIGGER IF EXISTS delivery_service_confirmation_no_delete ON delivery_serviceconfirmation')
            cursor.execute('DROP TRIGGER IF EXISTS delivery_service_month_protect ON delivery_serviceconfirmationmonth')
            cursor.execute('DROP FUNCTION IF EXISTS delivery_service_confirmation_protect()')
            cursor.execute('DROP FUNCTION IF EXISTS delivery_service_month_protect()')
        elif vendor == 'sqlite':
            for name in ['delivery_serviceconfirmationreversal_no_update', 'delivery_serviceconfirmationreversal_no_delete',
                         'delivery_service_confirmation_protect', 'delivery_service_confirmation_no_delete',
                         'delivery_service_month_no_insert', 'delivery_service_month_coverage',
                         'delivery_service_month_no_update', 'delivery_service_month_no_delete']:
                cursor.execute(f'DROP TRIGGER IF EXISTS {name}')


class Migration(migrations.Migration):
    dependencies = [('delivery', '0003_serviceconfirmation_serviceconfirmationmonth_and_more')]
    operations = [migrations.RunPython(install, uninstall)]
