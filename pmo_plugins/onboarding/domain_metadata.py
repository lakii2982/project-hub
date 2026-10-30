DOMAIN_METADATA = {
    'schema_version': '1', 'plugin_id': 'onboarding',
    'django_app': {'module': 'pmo_plugins.onboarding', 'label': 'onboarding'},
    'contracts': {'commands': [], 'queries': [], 'events': []},
    'migration_dependencies': ['core', 'contracts', 'settlement', 'cash'],
    'routes': [{'name': 'import_preview_list', 'path': 'import-preview-list/',
                'access': 'business_read'},
               {'name': 'import_preview_run', 'path': 'import-preview-run/',
                'access': 'history_read'},
               {'name': 'import_preview_upload', 'path': 'import-preview-upload/',
                'access': 'business_write'}],
}
