DOMAIN_METADATA = {
    'schema_version': '1', 'plugin_id': 'contracts',
    'django_app': {'module': 'pmo_plugins.contracts', 'label': 'contracts'},
    'contracts': {'commands': [], 'queries': [], 'events': []},
    'migration_dependencies': ['core'],
    'routes': [
        {'name': name.replace('-', '_'), 'path': name + '/', 'access': access}
        for name, access in (
            ('parties', 'business_read'), ('party', 'business_read'),
            ('party-save', 'business_write'), ('contact-save', 'business_write'),
            ('contracts', 'business_read'), ('contract', 'business_read'),
            ('contract-save', 'business_write'),
            ('version', 'business_read'), ('version-save', 'business_write'),
            ('version-submit', 'business_write'), ('version-review', 'business_write'),
            ('contract-changes', 'business_read'), ('contract-change', 'business_read'),
            ('contract-change-impact', 'history_read'),
            ('contract-change-save', 'business_write'), ('contract-change-review', 'business_write'),
        )
    ],
}
