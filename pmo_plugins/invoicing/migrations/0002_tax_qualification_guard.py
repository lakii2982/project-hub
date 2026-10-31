"""PH-320: allow only the append-only tax-qualification state transitions.

PH-252 made TaxQualification fully immutable. PH-320 needs pending -> confirmed
(and confirmed -> superseded when a replacement confirmed fact is appended)
without permitting edits to the source/amount/creator fields. This migration
replaces only the tax-qualification guards, leaving invoice/line/allocation/
reversal guards untouched.
"""
from django.db import migrations


TABLES = 'invoicing_taxqualification'


def install(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        if vendor == 'postgresql':
            cursor.execute(f'DROP TRIGGER IF EXISTS {TABLES}_guard ON {TABLES}')
            cursor.execute("DROP FUNCTION IF EXISTS invoicing_tax_qualification_guard_v2()")
            cursor.execute("""CREATE FUNCTION invoicing_tax_qualification_guard_v2() RETURNS trigger
                LANGUAGE plpgsql AS $$
                BEGIN
                  IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Tax qualification history cannot be deleted';
                  END IF;
                  IF TG_OP = 'INSERT' THEN
                    RETURN NEW;
                  END IF;
                  IF NEW.id <> OLD.id OR NEW.organization_id <> OLD.organization_id
                     OR NEW.source_ref <> OLD.source_ref OR NEW.tax_cents <> OLD.tax_cents
                     OR NEW.created_at <> OLD.created_at THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Tax qualification facts are immutable';
                  END IF;
                  IF OLD.status = 'pending' AND NEW.status = 'confirmed'
                     AND NEW.qualification_status IN ('recoverable', 'not_recoverable')
                     AND NEW.decided_on IS NOT NULL
                     AND NEW.version = OLD.version + 1 THEN
                    RETURN NEW;
                  END IF;
                  IF OLD.status = 'confirmed' AND NEW.status = 'superseded'
                     AND NEW.qualification_status = OLD.qualification_status
                     AND NEW.decided_on IS NOT DISTINCT FROM OLD.decided_on
                     AND NEW.evidence = OLD.evidence
                     AND NEW.version = OLD.version + 1 THEN
                    RETURN NEW;
                  END IF;
                  RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Only pending->confirmed or confirmed->superseded status transition is allowed';
                END; $$""")
            cursor.execute(f'CREATE TRIGGER {TABLES}_guard BEFORE INSERT OR UPDATE OR DELETE ON {TABLES} '
                           f'FOR EACH ROW EXECUTE FUNCTION invoicing_tax_qualification_guard_v2()')
        else:
            cursor.execute(f'DROP TRIGGER IF EXISTS {TABLES}_update_guard')
            cursor.execute(f'DROP TRIGGER IF EXISTS {TABLES}_no_delete')
            cursor.execute(f"""CREATE TRIGGER {TABLES}_update_guard BEFORE UPDATE ON {TABLES}
                FOR EACH ROW WHEN NEW.id <> OLD.id OR NEW.organization_id <> OLD.organization_id
                  OR NEW.source_ref <> OLD.source_ref OR NEW.tax_cents <> OLD.tax_cents
                  OR NEW.created_at <> OLD.created_at
                  OR NOT (
                    (OLD.status = 'pending' AND NEW.status = 'confirmed'
                     AND NEW.qualification_status IN ('recoverable', 'not_recoverable')
                     AND NEW.decided_on IS NOT NULL AND NEW.version = OLD.version + 1)
                    OR
                    (OLD.status = 'confirmed' AND NEW.status = 'superseded'
                     AND NEW.qualification_status = OLD.qualification_status
                     AND NEW.decided_on IS OLD.decided_on AND NEW.evidence = OLD.evidence
                     AND NEW.version = OLD.version + 1)
                  )
                BEGIN SELECT RAISE(ABORT, 'Only pending->confirmed or confirmed->superseded status transition is allowed'); END;""")
            cursor.execute(f"""CREATE TRIGGER {TABLES}_no_delete BEFORE DELETE ON {TABLES}
                FOR EACH ROW BEGIN SELECT RAISE(ABORT, 'Tax qualification history cannot be deleted'); END;""")


def uninstall(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        if vendor == 'postgresql':
            cursor.execute(f'DROP TRIGGER IF EXISTS {TABLES}_guard ON {TABLES}')
            cursor.execute("DROP FUNCTION IF EXISTS invoicing_tax_qualification_guard_v2()")
            cursor.execute(f"""CREATE FUNCTION invoicing_immutable_guard() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Invoicing fact history cannot be deleted';
                  END IF;
                  IF TG_OP = 'UPDATE' THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Invoicing facts are immutable';
                  END IF;
                  RETURN NEW;
                END; $$""")
            cursor.execute(f'CREATE TRIGGER {TABLES}_guard BEFORE UPDATE OR DELETE ON {TABLES} '
                           f'FOR EACH ROW EXECUTE FUNCTION invoicing_immutable_guard()')
        else:
            cursor.execute(f'DROP TRIGGER IF EXISTS {TABLES}_update_guard')
            cursor.execute(f'DROP TRIGGER IF EXISTS {TABLES}_no_delete')
            cursor.execute(f"""CREATE TRIGGER {TABLES}_update_guard BEFORE UPDATE ON {TABLES}
                FOR EACH ROW BEGIN SELECT RAISE(ABORT, 'Invoicing facts are immutable'); END;""")
            cursor.execute(f"""CREATE TRIGGER {TABLES}_no_delete BEFORE DELETE ON {TABLES}
                FOR EACH ROW BEGIN SELECT RAISE(ABORT, 'Invoicing fact history cannot be deleted'); END;""")


class Migration(migrations.Migration):
    dependencies = [('invoicing', '0001_initial')]
    operations = [migrations.RunPython(install, uninstall)]
