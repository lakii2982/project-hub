"""Explicit trusted business bindings. Called after Django has loaded installed Apps."""
from django.apps import apps
from core.plugin_sdk.migrations import prepare_plugin_entry_point
from .domain_metadata import DOMAIN_METADATA
from .plugin import Plugin
from . import party_views, views, approval_views


def entry_point():
    return prepare_plugin_entry_point([{
        'plugin_id': 'contracts', 'metadata': DOMAIN_METADATA, 'manifest': Plugin.manifest,
        'catalog': {'contract_catalog': []},
        'installed_apps': {app.label: app.name for app in apps.get_app_configs()},
    }], {'contracts': {
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
    }})
