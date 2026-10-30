"""Two explicit trusted domain bindings; no new loader or public commands."""
from django.apps import apps
from core.plugin_sdk.migrations import prepare_plugin_entry_point
from .domain_metadata import DOMAIN_METADATA
from .plugin import Plugin


def entry_point():
    from . import bank_charge_views, labor_views
    from pmo_plugins.contracts.domain_metadata import DOMAIN_METADATA as CONTRACTS_METADATA
    from pmo_plugins.contracts.plugin import Plugin as ContractsPlugin
    from pmo_plugins.contracts import party_views, views, approval_views
    installed = {app.label: app.name for app in apps.get_app_configs()}
    descriptors = [{'plugin_id': key, 'metadata': metadata, 'manifest': plugin.manifest,
        'catalog': {'contract_catalog': []}, 'installed_apps': installed}
        for key, metadata, plugin in [('contracts', CONTRACTS_METADATA, ContractsPlugin), ('costing', DOMAIN_METADATA, Plugin)]]
    return prepare_plugin_entry_point(descriptors, {
        'contracts': {
            'parties': party_views.party_list, 'party': party_views.party_detail,
            'party_save': party_views.party_save, 'contact_save': party_views.contact_save,
            'contracts': views.contract_list, 'contract': views.contract_detail,
            'contract_save': views.contract_save,
            'version': approval_views.version_detail, 'version_save': approval_views.version_save,
            'version_submit': approval_views.version_submit, 'version_review': approval_views.version_review,
        },
        'costing': {name: getattr(labor_views, name) for name in
                    ('labor_list', 'labor_detail', 'labor_form', 'labor_save', 'labor_action')}
                   | {name: getattr(bank_charge_views, name) for name in
                      ('bank_charge_list', 'bank_charge_form', 'bank_charge_save', 'bank_charge_action')},
    })
