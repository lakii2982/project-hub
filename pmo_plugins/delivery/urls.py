"""Two explicit trusted domain bindings; no new loader or public commands."""
from django.apps import apps
from core.plugin_sdk.migrations import prepare_plugin_entry_point
from .domain_metadata import DOMAIN_METADATA
from .plugin import Plugin


def entry_point():
    from pmo_plugins.costing import bank_charge_views, labor_views
    from . import service_period_views
    from . import views as delivery_views
    from pmo_plugins.costing.domain_metadata import DOMAIN_METADATA as COSTING_METADATA
    from pmo_plugins.costing.plugin import Plugin as CostingPlugin
    from pmo_plugins.management_revenue.domain_metadata import DOMAIN_METADATA as REVENUE_METADATA
    from pmo_plugins.management_revenue.plugin import Plugin as RevenuePlugin
    from pmo_plugins.settlement.domain_metadata import DOMAIN_METADATA as SETTLEMENT_METADATA
    from pmo_plugins.settlement.plugin import Plugin as SettlementPlugin
    from pmo_plugins.settlement import receivable_views
    from pmo_plugins.cash.domain_metadata import DOMAIN_METADATA as CASH_METADATA
    from pmo_plugins.cash.plugin import Plugin as CashPlugin
    from pmo_plugins.cash import views as cash_views
    from pmo_plugins.contracts.domain_metadata import DOMAIN_METADATA as CONTRACTS_METADATA
    from pmo_plugins.contracts.plugin import Plugin as ContractsPlugin
    from pmo_plugins.contracts import party_views, views, approval_views
    from pmo_plugins.time.domain_metadata import DOMAIN_METADATA as TIME_METADATA
    from pmo_plugins.time.plugin import Plugin as TimePlugin
    from pmo_plugins.time import views as time_views
    from pmo_plugins.onboarding.domain_metadata import DOMAIN_METADATA as ONBOARDING_METADATA
    from pmo_plugins.onboarding.plugin import Plugin as OnboardingPlugin
    from pmo_plugins.onboarding import views as onboarding_views
    installed = {app.label: app.name for app in apps.get_app_configs()}
    descriptors = [{'plugin_id': key, 'metadata': metadata, 'manifest': plugin.manifest,
        'catalog': {'contract_catalog': []}, 'installed_apps': installed}
        for key, metadata, plugin in [('contracts', CONTRACTS_METADATA, ContractsPlugin), ('costing', COSTING_METADATA, CostingPlugin), ('delivery', DOMAIN_METADATA, Plugin), ('management_revenue', REVENUE_METADATA, RevenuePlugin), ('settlement', SETTLEMENT_METADATA, SettlementPlugin), ('cash', CASH_METADATA, CashPlugin), ('time', TIME_METADATA, TimePlugin), ('onboarding', ONBOARDING_METADATA, OnboardingPlugin)]]
    return prepare_plugin_entry_point(descriptors, {
        'contracts': {
            'parties': party_views.party_list, 'party': party_views.party_detail,
            'party_save': party_views.party_save, 'contact_save': party_views.contact_save,
            'contracts': views.contract_list, 'contract': views.contract_detail,
            'contract_save': views.contract_save,
            'version': approval_views.version_detail, 'version_save': approval_views.version_save,
            'version_submit': approval_views.version_submit, 'version_review': approval_views.version_review,
            'contract_changes': approval_views.contract_changes,
            'contract_change': approval_views.contract_change_detail,
            'contract_change_save': approval_views.contract_change_save,
            'contract_change_review': approval_views.contract_change_review,
            'contract_change_impact': views.contract_change_impact_detail,
            'contract_terms_snapshot': views.contract_terms_snapshot,
        },
        'management_revenue': {},
        'onboarding': {name: getattr(onboarding_views, name) for name in
                       ('import_preview_list', 'import_preview_run', 'import_preview_upload')},
        'settlement': {name: getattr(receivable_views, name) for name in ('settlement_list', 'settlement_detail', 'settlement_form', 'settlement_save', 'settlement_action', 'aging_list', 'clearance_create', 'clearance_reverse', 'clearance_list', 'offset_list', 'offset_save', 'settlement_coverage_snapshot', 'settlement_coverage_issues')},
        'cash': {name: getattr(cash_views, name) for name in ('funding_account_list', 'funding_account_form', 'funding_account_save', 'funding_account_activate', 'cash_transaction_list', 'cash_transaction_form', 'cash_transaction_save', 'cash_transaction_detail', 'cash_party_match', 'cash_transaction_record', 'cash_refund_list',
                              'cash_refund_form', 'cash_refund_save', 'cash_bank_return_form',
                              'cash_bank_return_save', 'cash_classification_list',
                              'cash_classification_form', 'cash_classification_save',
                              'cash_classification_reverse',
                              'cash_classification_reverse_save',
                              'cash_internal_transfer_list', 'cash_internal_transfer_form',
                              'cash_internal_transfer_save',
                              'cash_internal_transfer_reverse',
                              'cash_internal_transfer_reverse_save', 'cash_bridge_snapshot', 'cash_bridge_issues')},
        'delivery': {name: getattr(delivery_views,name) for name in ('acceptance_list','acceptance_detail','acceptance_form','acceptance_save','acceptance_action','ops_reconciliation_overview')} | {name: getattr(service_period_views,name) for name in ('service_confirmation_list','service_confirmation_detail','service_confirmation_form','service_confirmation_save','service_confirmation_action')},
        'costing': {name: getattr(labor_views, name) for name in
                    ('labor_list', 'labor_detail', 'labor_form', 'labor_save', 'labor_action')}
                   | {name: getattr(bank_charge_views, name) for name in
                      ('bank_charge_list', 'bank_charge_form', 'bank_charge_save', 'bank_charge_action',
                                 'bank_charge_pairing')},
        'time': {name: getattr(time_views, name) for name in
                 ('billable_confirmation_list', 'billable_confirmation_form',
                  'billable_confirmation_save', 'billable_confirmation_confirm',
                  'billable_confirmation_detail', 'billable_confirmation_reverse')},
    })