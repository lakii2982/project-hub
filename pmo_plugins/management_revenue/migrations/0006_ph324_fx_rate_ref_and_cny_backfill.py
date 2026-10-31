# Generated for PH-324: fx_rate_ref + CNY original-amount backfill.

from django.db import migrations, models

BACKFILL = [
        ('revenueadjustment', 'original_adjustment_cents', 'adjustment_cents'),
        ('revenuerecognition', 'original_recognized_net_cents', 'recognized_net_cents'),
]


def add_fx_rate_ref(apps, schema_editor):
    quote = schema_editor.quote_name
    column_type = "jsonb" if schema_editor.connection.vendor == "postgresql" else "json"
    for model in ['revenueadjustment', 'revenuerecognition']:
        table = "management_revenue_" + model
        schema_editor.execute(
            f"ALTER TABLE {quote(table)} ADD COLUMN {quote('fx_rate_ref')} {column_type} NULL"
        )


def drop_fx_rate_ref(apps, schema_editor):
    quote = schema_editor.quote_name
    for model in ['revenueadjustment', 'revenuerecognition']:
        table = "management_revenue_" + model
        schema_editor.execute(f"ALTER TABLE {quote(table)} DROP COLUMN {quote('fx_rate_ref')}")


def backfill_original_cny(apps, schema_editor):
    quote = schema_editor.quote_name
    for model, original, base in BACKFILL:
        table = "management_revenue_" + model
        schema_editor.execute(
            f"UPDATE {quote(table)} SET {quote(original)} = {quote(base)} "
            f"WHERE {quote('currency')} = 'CNY' AND {quote(original)} IS NULL"
        )


class Migration(migrations.Migration):

    dependencies = [
        ('management_revenue', '0005_revenueadjustment_currency_and_more'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
        migrations.AddField(model_name='revenueadjustment', name='fx_rate_ref', field=models.JSONField(default=None, null=True)),
        migrations.AddField(model_name='revenuerecognition', name='fx_rate_ref', field=models.JSONField(default=None, null=True)),
            ],
            database_operations=[
                migrations.RunPython(add_fx_rate_ref, drop_fx_rate_ref),
                migrations.RunPython(backfill_original_cny, migrations.RunPython.noop),
            ],
        ),
    ]
