from django.db import migrations


def install(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        if vendor == 'postgresql':
            cursor.execute("""CREATE FUNCTION management_revenue_service_source_guard() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  IF TG_OP = 'UPDATE' AND (OLD.acceptance_id IS DISTINCT FROM NEW.acceptance_id
                      OR OLD.service_confirmation_id IS DISTINCT FROM NEW.service_confirmation_id
                      OR OLD.service_confirmation_month_id IS DISTINCT FROM NEW.service_confirmation_month_id)
                  THEN RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Revenue source identity is immutable'; END IF;
                  IF NEW.service_confirmation_month_id IS NOT NULL THEN
                    IF NEW.acceptance_id IS NOT NULL OR NEW.service_confirmation_id IS NULL
                    THEN RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Service revenue requires a real month and no acceptance'; END IF;
                    IF NOT EXISTS(SELECT 1 FROM delivery_serviceconfirmationmonth m
                        JOIN delivery_serviceconfirmation p ON p.id = m.service_confirmation_id
                        WHERE m.id = NEW.service_confirmation_month_id AND m.service_confirmation_id = NEW.service_confirmation_id
                          AND p.organization_id = NEW.organization_id AND p.project_id = NEW.project_id
                          AND m.allocated_net_cents = NEW.recognized_net_cents
                          AND m.coverage_end = NEW.occurred_on AND m.service_month = NEW.posting_period)
                    THEN RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Service month does not match its confirmation or amount'; END IF;
                  END IF;
                  RETURN NEW; END; $$""")
            cursor.execute('CREATE TRIGGER management_revenue_service_source_guard BEFORE INSERT OR UPDATE ON management_revenue_revenuerecognition FOR EACH ROW EXECUTE FUNCTION management_revenue_service_source_guard()')
        elif vendor == 'sqlite':
            cursor.execute("""CREATE TRIGGER management_revenue_service_source_immutable BEFORE UPDATE ON management_revenue_revenuerecognition
                WHEN OLD.acceptance_id IS NOT NEW.acceptance_id
                  OR OLD.service_confirmation_id IS NOT NEW.service_confirmation_id
                  OR OLD.service_confirmation_month_id IS NOT NEW.service_confirmation_month_id
                BEGIN SELECT RAISE(ABORT, 'Revenue source identity is immutable'); END""")
            for action in ['INSERT', 'UPDATE']:
                cursor.execute(f"""CREATE TRIGGER management_revenue_service_source_match_{action.lower()} BEFORE {action} ON management_revenue_revenuerecognition
                    WHEN NEW.service_confirmation_month_id IS NOT NULL AND NOT EXISTS(
                      SELECT 1 FROM delivery_serviceconfirmationmonth m
                      JOIN delivery_serviceconfirmation p ON p.id = m.service_confirmation_id
                      WHERE m.id = NEW.service_confirmation_month_id AND m.service_confirmation_id = NEW.service_confirmation_id
                        AND p.organization_id = NEW.organization_id AND p.project_id = NEW.project_id
                        AND m.allocated_net_cents = NEW.recognized_net_cents
                        AND m.coverage_end = NEW.occurred_on AND m.service_month = NEW.posting_period)
                    BEGIN SELECT RAISE(ABORT, 'Service month does not match its confirmation or amount'); END""")
            cursor.execute("""CREATE TRIGGER management_revenue_service_source_acceptance BEFORE INSERT ON management_revenue_revenuerecognition
                WHEN NEW.service_confirmation_month_id IS NOT NULL AND NEW.acceptance_id IS NOT NULL
                BEGIN SELECT RAISE(ABORT, 'Service revenue requires a real month and no acceptance'); END""")


def uninstall(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        if vendor == 'postgresql':
            cursor.execute('DROP TRIGGER IF EXISTS management_revenue_service_source_guard ON management_revenue_revenuerecognition')
            cursor.execute('DROP FUNCTION IF EXISTS management_revenue_service_source_guard()')
        elif vendor == 'sqlite':
            for name in ['management_revenue_service_source_immutable', 'management_revenue_service_source_match_insert',
                         'management_revenue_service_source_match_update', 'management_revenue_service_source_acceptance']:
                cursor.execute(f'DROP TRIGGER IF EXISTS {name}')


class Migration(migrations.Migration):
    dependencies = [('management_revenue', '0003_revenuerecognition_service_confirmation_and_more'),
                    ('delivery', '0003_serviceconfirmation_serviceconfirmationmonth_and_more')]
    operations = [migrations.RunPython(install, uninstall)]
