from django.db import migrations


FULL = ['costing_costpolicy', 'costing_costevent', 'costing_laborreceipt']
FROZEN = {
    'costing_allocationtarget': ('TRUE', '1', ['organization_id', 'allocation_run_id', 'target_kind', 'target_id', 'weight', 'allocated_cents']),
    'costing_salarypool': (
        "OLD.approved_salary_snapshot::text <> '{}'", "OLD.approved_salary_snapshot <> '{}'",
        ['organization_id', 'employee_id', 'period', 'covered_through', 'valuation_state',
         'created_by_id', 'submitted_by_id', 'approved_by_id', 'approved_at', 'approval_audit_id',
         'approved_source_version', 'approved_salary_snapshot', 'original_policy_id', 'draft_components', 'reference']),
    'costing_allocationrun': ('TRUE', '1',
        ['organization_id', 'salary_pool_id', 'capacity_policy_id', 'created_by_id', 'approved_inputs_snapshot',
         'allocation_snapshot', 'total_cents', 'algorithm_version']),
    'core_timesheet': (
        "OLD.accounting_mode = 'monthly_pool' AND OLD.approved_input_snapshot::text <> '{}'",
        "OLD.accounting_mode = 'monthly_pool' AND OLD.approved_input_snapshot <> '{}'",
        ['organization_id', 'employee_id', 'project_id', 'non_project_destination_id', 'work_date',
         'minutes', 'accounting_mode', 'hourly_rate_cents', 'cost_cents', 'approved_input_snapshot', 'submitted_by_id']),
}


def install(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        for table in FULL:
            if vendor == 'postgresql':
                cursor.execute(f'CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION pmo_prevent_mutation()')
            elif vendor == 'sqlite':
                for action in ['UPDATE', 'DELETE']:
                    cursor.execute(f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'Labor facts are immutable'); END")
        for table, (pg_when, sqlite_when, fields) in FROZEN.items():
            if vendor == 'postgresql':
                changed = ' OR '.join(f'OLD.{field} IS DISTINCT FROM NEW.{field}' for field in fields)
                cursor.execute(f"""CREATE FUNCTION {table}_protect_facts() RETURNS trigger LANGUAGE plpgsql AS $$
                    BEGIN IF {pg_when} AND ({changed}) THEN RAISE EXCEPTION 'Approved labor inputs are immutable'; END IF;
                    RETURN NEW; END; $$""")
                cursor.execute(f'CREATE TRIGGER {table}_protect_facts BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION {table}_protect_facts()')
                cursor.execute(f'CREATE TRIGGER {table}_protect_delete BEFORE DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION pmo_prevent_mutation()')
            elif vendor == 'sqlite':
                changed = ' OR '.join(f'OLD.{field} IS NOT NEW.{field}' for field in fields)
                cursor.execute(f"CREATE TRIGGER {table}_protect_facts BEFORE UPDATE ON {table} WHEN ({sqlite_when}) AND ({changed}) BEGIN SELECT RAISE(ABORT, 'Approved labor inputs are immutable'); END")
                cursor.execute(f"CREATE TRIGGER {table}_protect_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT, 'Labor facts cannot be deleted'); END")


def uninstall(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        for table in FULL:
            if schema_editor.connection.vendor == 'postgresql':
                cursor.execute(f'DROP TRIGGER IF EXISTS {table}_immutable ON {table}')
            elif schema_editor.connection.vendor == 'sqlite':
                for action in ['update', 'delete']:
                    cursor.execute(f'DROP TRIGGER IF EXISTS {table}_no_{action}')
        for table in FROZEN:
            for suffix in ['protect_facts', 'protect_delete']:
                cursor.execute(f'DROP TRIGGER IF EXISTS {table}_{suffix}' + (f' ON {table}' if schema_editor.connection.vendor == 'postgresql' else ''))
            if schema_editor.connection.vendor == 'postgresql':
                cursor.execute(f'DROP FUNCTION IF EXISTS {table}_protect_facts()')


class Migration(migrations.Migration):
    dependencies = [('core', '0007_labor_append_only'), ('costing', '0001_initial')]
    operations = [migrations.RunPython(install, uninstall)]
