DOMAIN_METADATA = {
    'schema_version': '1', 'plugin_id': 'time',
    'django_app': {'module': 'pmo_plugins.time', 'label': 'time'},
    'contracts': {'commands': [], 'queries': [], 'events': []},
    'migration_dependencies': ['core', 'contracts'],
    'routes': [
        {'name': name, 'path': name.replace('_', '-') + '/', 'access': access}
        for name, access in (
            ('billable_confirmation_list', 'business_read'),
            ('billable_confirmation_form', 'business_read'),
            ('billable_confirmation_save', 'business_write'),
            ('billable_confirmation_confirm', 'business_write'),
        )
    ],
}
