# Generated for PH-324: fx_rate_ref + CNY original-amount backfill.

from django.db import migrations, models

BACKFILL = [
        ('contractchange', 'original_gross_delta_cents', 'gross_delta_cents'),
        ('contractchange', 'original_net_delta_cents', 'net_delta_cents'),
        ('contractchange', 'original_tax_delta_cents', 'tax_delta_cents'),
        ('contractline', 'original_gross_cents', 'gross_cents'),
        ('contractline', 'original_net_cents', 'net_cents'),
        ('contractline', 'original_tax_cents', 'tax_cents'),
        ('paymentterm', 'original_gross_cents', 'gross_cents'),
        ('warrantyterm', 'original_gross_cents', 'gross_cents'),
]


def add_fx_rate_ref(apps, schema_editor):
    quote = schema_editor.quote_name
    column_type = "jsonb" if schema_editor.connection.vendor == "postgresql" else "json"
    for model in ['contractchange', 'contractline', 'paymentterm', 'warrantyterm']:
        table = "contracts_" + model
        schema_editor.execute(
            f"ALTER TABLE {quote(table)} ADD COLUMN {quote('fx_rate_ref')} {column_type} NULL"
        )


def drop_fx_rate_ref(apps, schema_editor):
    quote = schema_editor.quote_name
    for model in ['contractchange', 'contractline', 'paymentterm', 'warrantyterm']:
        table = "contracts_" + model
        schema_editor.execute(f"ALTER TABLE {quote(table)} DROP COLUMN {quote('fx_rate_ref')}")


def backfill_original_cny(apps, schema_editor):
    quote = schema_editor.quote_name
    for model, original, base in BACKFILL:
        table = "contracts_" + model
        schema_editor.execute(
            f"UPDATE {quote(table)} SET {quote(original)} = {quote(base)} "
            f"WHERE {quote('currency')} = 'CNY' AND {quote(original)} IS NULL"
        )


class Migration(migrations.Migration):

    dependencies = [
        ('contracts', '0005_contractchange_currency_and_more'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
        migrations.AddField(model_name='contractchange', name='fx_rate_ref', field=models.JSONField(default=None, null=True)),
        migrations.AddField(model_name='contractline', name='fx_rate_ref', field=models.JSONField(default=None, null=True)),
        migrations.AddField(model_name='paymentterm', name='fx_rate_ref', field=models.JSONField(default=None, null=True)),
        migrations.AddField(model_name='warrantyterm', name='fx_rate_ref', field=models.JSONField(default=None, null=True)),
            ],
            database_operations=[
                migrations.RunPython(add_fx_rate_ref, drop_fx_rate_ref),
                migrations.RunPython(backfill_original_cny, migrations.RunPython.noop),
            ],
        ),
    ]
