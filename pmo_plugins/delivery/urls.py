"""Two explicit trusted domain bindings; no new loader or public commands."""
from django.apps import apps
from core.plugin_sdk.migrations import prepare_plugin_entry_point
from .domain_metadata import DOMAIN_METADATA
from .plugin import Plugin


def entry_point():
    from pmo_plugins.costing import labor_views
    from . import views as delivery_views
    from pmo_plugins.costing.domain_metadata import DOMAIN_METADATA as COSTING_METADATA
    from pmo_plugins.costing.plugin import Plugin as CostingPlugin
    from pmo_plugins.management_revenue.domain_metadata import DOMAIN_METADATA as REVENUE_METADATA
    from pmo_plugins.management_revenue.plugin import Plugin as RevenuePlugin
    from pmo_plugins.contracts.domain_metadata import DOMAIN_METADATA as CONTRACTS_METADATA
    from pmo_plugins.contracts.plugin import Plugin as ContractsPlugin
    from pmo_plugins.contracts import party_views, views, approval_views
    installed = {app.label: app.name for app in apps.get_app_configs()}
    descriptors = [{'plugin_id': key, 'metadata': metadata, 'manifest': plugin.manifest,
        'catalog': {'contract_catalog': []}, 'installed_apps': installed}
        for key, metadata, plugin in [('contracts', CONTRACTS_METADATA, ContractsPlugin), ('costing', COSTING_METADATA, CostingPlugin), ('delivery', DOMAIN_METADATA, Plugin), ('management_revenue', REVENUE_METADATA, RevenuePlugin)]]
    return prepare_plugin_entry_point(descriptors, {
        'contracts': {
            'parties': party_views.party_list, 'party': party_views.party_detail,
            'party_save': party_views.party_save, 'contact_save': party_views.contact_save,
            'contracts': views.contract_list, 'contract': views.contract_detail,
            'contract_save': views.contract_save,
            'version': approval_views.version_detail, 'version_save': approval_views.version_save,
            'version_submit': approval_views.version_submit, 'version_review': approval_views.version_review,
        },
        'management_revenue': {},
        'delivery': {name: getattr(delivery_views,name) for name in ('acceptance_list','acceptance_detail','acceptance_form','acceptance_save','acceptance_action')},
        'costing': {name: getattr(labor_views, name) for name in
                    ('labor_list', 'labor_detail', 'labor_form', 'labor_save', 'labor_action')},
    })
