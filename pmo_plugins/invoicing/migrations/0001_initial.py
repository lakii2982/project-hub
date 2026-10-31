# Generated for PH-252 invoicing first slice.
import django.db.models.deletion
import uuid
from django.db import migrations, models


def install(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        if vendor == 'postgresql':
            cursor.execute('DROP FUNCTION IF EXISTS invoicing_invoice_guard()')
            cursor.execute('DROP FUNCTION IF EXISTS invoicing_immutable_guard()')
            cursor.execute("""CREATE FUNCTION invoicing_invoice_guard() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Invoice history cannot be deleted';
                  END IF;
                  IF TG_OP = 'INSERT' THEN
                    IF NEW.status <> 'active' OR NEW.version <> 1 THEN
                      RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Invoice must start active with version 1';
                    END IF;
                    RETURN NEW;
                  END IF;
                  IF NEW.id <> OLD.id OR NEW.organization_id <> OLD.organization_id
                     OR NEW.direction <> OLD.direction OR NEW.counterparty_id <> OLD.counterparty_id
                     OR NEW.invoice_number <> OLD.invoice_number OR NEW.invoice_date <> OLD.invoice_date
                     OR NEW.gross_cents <> OLD.gross_cents OR NEW.net_cents <> OLD.net_cents
                     OR NEW.tax_cents <> OLD.tax_cents OR NEW.version <> OLD.version
                     OR NEW.created_by_id <> OLD.created_by_id OR NEW.created_at <> OLD.created_at THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Invoice facts are immutable';
                  END IF;
                  IF OLD.status <> 'active' OR NEW.status <> 'reversed' THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Only active -> reversed status transition is allowed';
                  END IF;
                  RETURN NEW;
                END; $$""")
            cursor.execute('CREATE TRIGGER invoicing_invoice_guard BEFORE INSERT OR UPDATE OR DELETE ON invoicing_invoice FOR EACH ROW EXECUTE FUNCTION invoicing_invoice_guard()')
            cursor.execute("""CREATE FUNCTION invoicing_immutable_guard() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Invoicing fact history cannot be deleted';
                  END IF;
                  IF TG_OP = 'UPDATE' THEN
                    RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Invoicing facts are immutable';
                  END IF;
                  RETURN NEW;
                END; $$""")
            for table in ('invoicing_invoiceline', 'invoicing_invoiceallocation',
                          'invoicing_invoicereversal', 'invoicing_taxqualification'):
                cursor.execute(f'CREATE TRIGGER {table}_guard BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION invoicing_immutable_guard()')
        elif vendor == 'sqlite':
            cursor.execute("""CREATE TRIGGER invoicing_invoice_insert_guard BEFORE INSERT ON invoicing_invoice
                FOR EACH ROW WHEN NEW.status <> 'active' OR NEW.version <> 1
                BEGIN SELECT RAISE(ABORT, 'Invoice must start active with version 1'); END;""")
            cursor.execute("""CREATE TRIGGER invoicing_invoice_update_guard BEFORE UPDATE ON invoicing_invoice
                FOR EACH ROW WHEN NEW.id <> OLD.id OR NEW.organization_id <> OLD.organization_id
                  OR NEW.direction <> OLD.direction OR NEW.counterparty_id <> OLD.counterparty_id
                  OR NEW.invoice_number <> OLD.invoice_number OR NEW.invoice_date <> OLD.invoice_date
                  OR NEW.gross_cents <> OLD.gross_cents OR NEW.net_cents <> OLD.net_cents
                  OR NEW.tax_cents <> OLD.tax_cents OR NEW.version <> OLD.version
                  OR NEW.created_by_id <> OLD.created_by_id OR NEW.created_at <> OLD.created_at
                  OR OLD.status <> 'active' OR NEW.status <> 'reversed'
                BEGIN SELECT RAISE(ABORT, 'Invoice facts are immutable'); END;""")
            cursor.execute("""CREATE TRIGGER invoicing_invoice_no_delete BEFORE DELETE ON invoicing_invoice
                FOR EACH ROW BEGIN SELECT RAISE(ABORT, 'Invoice history cannot be deleted'); END;""")
            for table in ('invoicing_invoiceline', 'invoicing_invoiceallocation',
                          'invoicing_invoicereversal', 'invoicing_taxqualification'):
                cursor.execute(f"""CREATE TRIGGER {table}_update_guard BEFORE UPDATE ON {table}
                    FOR EACH ROW BEGIN SELECT RAISE(ABORT, 'Invoicing facts are immutable'); END;""")
                cursor.execute(f"""CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table}
                    FOR EACH ROW BEGIN SELECT RAISE(ABORT, 'Invoicing fact history cannot be deleted'); END;""")


def uninstall(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        if vendor == 'postgresql':
            for table in ('invoicing_invoice', 'invoicing_invoiceline',
                          'invoicing_invoiceallocation', 'invoicing_invoicereversal',
                          'invoicing_taxqualification'):
                cursor.execute(f'DROP TRIGGER IF EXISTS {table}_guard ON {table}')
            cursor.execute('DROP FUNCTION IF EXISTS invoicing_invoice_guard()')
            cursor.execute('DROP FUNCTION IF EXISTS invoicing_immutable_guard()')
        elif vendor == 'sqlite':
            for name in ('invoicing_invoice_insert_guard', 'invoicing_invoice_update_guard',
                         'invoicing_invoice_no_delete', 'invoicing_invoiceline_update_guard',
                         'invoicing_invoiceline_no_delete',
                         'invoicing_invoiceallocation_update_guard',
                         'invoicing_invoiceallocation_no_delete',
                         'invoicing_invoicereversal_update_guard',
                         'invoicing_invoicereversal_no_delete',
                         'invoicing_taxqualification_update_guard',
                         'invoicing_taxqualification_no_delete'):
                cursor.execute(f'DROP TRIGGER IF EXISTS {name}')


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        ('core', '0010_membership_version'),
        ('contracts', '0003_contract_terminated_on_contractchange_and_more'),
    ]
    operations = [
        migrations.CreateModel(name='Invoice', fields=[
            ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ('direction', models.CharField(max_length=8)),
            ('invoice_number', models.CharField(max_length=80)),
            ('invoice_date', models.DateField()),
            ('gross_cents', models.BigIntegerField()),
            ('net_cents', models.BigIntegerField()),
            ('tax_cents', models.BigIntegerField()),
            ('status', models.CharField(default='active', max_length=16)),
            ('version', models.PositiveIntegerField(default=1)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('counterparty', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='invoices', to='contracts.counterparty')),
            ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='core.membership')),
            ('organization', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='invoices', to='core.organization')),
        ], options={'constraints': [
            models.UniqueConstraint(fields=['organization', 'direction', 'invoice_number'], name='invoicing_invoice_number_unique'),
            models.CheckConstraint(condition=models.Q(direction__in=['sales', 'purchase']), name='invoicing_invoice_direction'),
            models.CheckConstraint(condition=models.Q(status__in=['draft', 'active', 'posted', 'reversed', 'superseded']), name='invoicing_invoice_status'),
            models.CheckConstraint(condition=models.Q(gross_cents__gt=0, gross_cents__lte=10 ** 12), name='invoicing_invoice_gross_bound'),
            models.CheckConstraint(condition=models.Q(net_cents__gte=0, tax_cents__gte=0, gross_cents=models.F('net_cents') + models.F('tax_cents')), name='invoicing_invoice_tax_split'),
        ]}),
        migrations.CreateModel(name='InvoiceLine', fields=[
            ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ('line_no', models.PositiveIntegerField()),
            ('gross_cents', models.BigIntegerField()),
            ('net_cents', models.BigIntegerField()),
            ('tax_cents', models.BigIntegerField()),
            ('status', models.CharField(default='active', max_length=16)),
            ('version', models.PositiveIntegerField(default=1)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('invoice', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='lines', to='invoicing.invoice')),
            ('organization', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='invoice_lines', to='core.organization')),
        ], options={'constraints': [
            models.UniqueConstraint(fields=['invoice', 'line_no'], name='invoicing_line_no_unique'),
            models.CheckConstraint(condition=models.Q(line_no__gte=1), name='invoicing_line_no'),
            models.CheckConstraint(condition=models.Q(status__in=['draft', 'active', 'posted', 'reversed', 'superseded']), name='invoicing_line_status'),
            models.CheckConstraint(condition=models.Q(gross_cents__gt=0, gross_cents__lte=10 ** 12), name='invoicing_line_gross_bound'),
            models.CheckConstraint(condition=models.Q(net_cents__gte=0, tax_cents__gte=0, gross_cents=models.F('net_cents') + models.F('tax_cents')), name='invoicing_line_tax_split'),
        ]}),
        migrations.CreateModel(name='InvoiceAllocation', fields=[
            ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ('business_source_ref', models.JSONField()),
            ('gross_cents', models.BigIntegerField()),
            ('net_cents', models.BigIntegerField()),
            ('tax_cents', models.BigIntegerField()),
            ('status', models.CharField(default='active', max_length=16)),
            ('version', models.PositiveIntegerField(default=1)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='core.membership')),
            ('invoice_line', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='allocations', to='invoicing.invoiceline')),
            ('organization', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='invoice_allocations', to='core.organization')),
        ], options={'constraints': [
            models.CheckConstraint(condition=models.Q(status__in=['draft', 'active', 'posted', 'reversed', 'superseded']), name='invoicing_allocation_status'),
            models.CheckConstraint(condition=models.Q(gross_cents__gt=0, gross_cents__lte=10 ** 12), name='invoicing_allocation_gross_bound'),
            models.CheckConstraint(condition=models.Q(net_cents__gte=0, tax_cents__gte=0, gross_cents=models.F('net_cents') + models.F('tax_cents')), name='invoicing_allocation_tax_split'),
        ]}),
        migrations.CreateModel(name='InvoiceReversal', fields=[
            ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ('reason', models.CharField(max_length=500)),
            ('reversal_date', models.DateField()),
            ('line_reversals', models.JSONField(default=list)),
            ('allocation_reversals', models.JSONField(default=list)),
            ('gross_cents', models.BigIntegerField()),
            ('net_cents', models.BigIntegerField()),
            ('tax_cents', models.BigIntegerField()),
            ('status', models.CharField(default='active', max_length=16)),
            ('version', models.PositiveIntegerField(default=1)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='core.membership')),
            ('invoice', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='reversals', to='invoicing.invoice')),
            ('organization', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='invoice_reversals', to='core.organization')),
        ], options={'constraints': [
            models.CheckConstraint(condition=models.Q(status__in=['draft', 'active', 'posted', 'reversed', 'superseded']), name='invoicing_reversal_status'),
            models.CheckConstraint(condition=models.Q(gross_cents__lt=0, net_cents__lte=0, tax_cents__lte=0, gross_cents=models.F('net_cents') + models.F('tax_cents')), name='invoicing_reversal_signed'),
        ]}),
        migrations.CreateModel(name='TaxQualification', fields=[
            ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ('source_ref', models.JSONField()),
            ('tax_cents', models.BigIntegerField()),
            ('status', models.CharField(default='pending', max_length=16)),
            ('qualification_status', models.CharField(default='unknown', max_length=20)),
            ('decided_on', models.DateField(blank=True, null=True)),
            ('evidence', models.JSONField(default=list)),
            ('version', models.PositiveIntegerField(default=1)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('organization', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='tax_qualifications', to='core.organization')),
        ], options={'constraints': [
            models.CheckConstraint(condition=models.Q(status__in=['pending', 'confirmed', 'superseded']), name='invoicing_tax_status'),
            models.CheckConstraint(condition=models.Q(qualification_status__in=['unknown', 'recoverable', 'not_recoverable']), name='invoicing_tax_qualification'),
            models.CheckConstraint(condition=models.Q(tax_cents__gte=0, tax_cents__lte=10 ** 12), name='invoicing_tax_bound'),
        ]}),
        migrations.RunPython(install, uninstall),
    ]
