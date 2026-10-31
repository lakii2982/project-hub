# Generated for PH-324: fx_rate_ref + CNY original-amount backfill.

from django.db import migrations, models

BACKFILL = [
        ('bankreturn', 'original_gross_cents', 'gross_cents'),
        ('cashtransaction', 'original_gross_cents', 'gross_cents'),
        ('refund', 'original_gross_cents', 'gross_cents'),
]


def add_fx_rate_ref(apps, schema_editor):
    quote = schema_editor.quote_name
    column_type = "jsonb" if schema_editor.connection.vendor == "postgresql" else "json"
    for model in ['bankreturn', 'cashtransaction', 'refund']:
        table = "cash_" + model
        schema_editor.execute(
            f"ALTER TABLE {quote(table)} ADD COLUMN {quote('fx_rate_ref')} {column_type} NULL"
        )


def drop_fx_rate_ref(apps, schema_editor):
    quote = schema_editor.quote_name
    for model in ['bankreturn', 'cashtransaction', 'refund']:
        table = "cash_" + model
        schema_editor.execute(f"ALTER TABLE {quote(table)} DROP COLUMN {quote('fx_rate_ref')}")


def backfill_original_cny(apps, schema_editor):
    quote = schema_editor.quote_name
    for model, original, base in BACKFILL:
        table = "cash_" + model
        schema_editor.execute(
            f"UPDATE {quote(table)} SET {quote(original)} = {quote(base)} "
            f"WHERE {quote('currency')} = 'CNY' AND {quote(original)} IS NULL"
        )


class Migration(migrations.Migration):

    dependencies = [
        ('cash', '0008_bankreturn_currency_bankreturn_original_gross_cents_and_more'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
        migrations.AddField(model_name='bankreturn', name='fx_rate_ref', field=models.JSONField(default=None, null=True)),
        migrations.AddField(model_name='cashtransaction', name='fx_rate_ref', field=models.JSONField(default=None, null=True)),
        migrations.AddField(model_name='refund', name='fx_rate_ref', field=models.JSONField(default=None, null=True)),
            ],
            database_operations=[
                migrations.RunPython(add_fx_rate_ref, drop_fx_rate_ref),
                migrations.RunPython(backfill_original_cny, migrations.RunPython.noop),
            ],
        ),
    ]
