DOMAIN_METADATA = {
    'schema_version': '1', 'plugin_id': 'insights',
    'django_app': {'module': 'pmo_plugins.insights', 'label': 'insights'},
    'contracts': {'commands': [], 'queries': [], 'events': []},
    'migration_dependencies': ['core', 'cash', 'settlement', 'costing', 'contracts'],
    'routes': [
        {'name': 'period_precheck', 'path': 'period-precheck/', 'access': 'history_read'},
    ],
}
