DOMAIN_METADATA = {
    'schema_version': '1', 'plugin_id': 'costing',
    'django_app': {'module': 'pmo_plugins.costing', 'label': 'costing'},
    'contracts': {'commands': [], 'queries': [], 'events': []},
    'migration_dependencies': ['core'],
    'routes': [{'name': name.replace('-', '_'), 'path': name + '/', 'access': access}
               for name, access in [('labor-list', 'business_read'), ('labor-detail', 'business_read'),
                   ('labor-form', 'business_read'), ('labor-save', 'business_write'), ('labor-action', 'business_write'),
                   ('bank-charge-list', 'business_read'), ('bank-charge-form', 'business_read'),
                   ('bank-charge-save', 'business_write'), ('bank-charge-action', 'business_write')]],
}
