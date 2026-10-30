DOMAIN_METADATA = {
    'schema_version': '1', 'plugin_id': 'onboarding',
    'django_app': {'module': 'pmo_plugins.onboarding', 'label': 'onboarding'},
    'contracts': {'commands': [], 'queries': [], 'events': []},
    # ADR 0012 freezes ['core','contracts','settlement','cash']; kept empty for this
    # preview-only bootstrap while the manifest requires guard is deferred (see plugin.py);
    # the equivalent dependency gate lives in preview_services._fresh.
    'migration_dependencies': [],
    'routes': [{'name': name, 'path': name.replace('_', '-') + '/',
                'access': 'business_read' if name == 'import_preview_list'
                          else 'business_write'}
               for name in ('import_preview_list', 'import_preview_run')],
}
