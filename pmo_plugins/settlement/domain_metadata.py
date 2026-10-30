DOMAIN_METADATA = {
    'schema_version': '1', 'plugin_id': 'settlement',
    'django_app': {'module': 'pmo_plugins.settlement', 'label': 'settlement'},
    'contracts': {'commands': [], 'queries': [], 'events': []},
    'migration_dependencies': ['core', 'contracts', 'delivery'],
    'routes': [{'name': name, 'path': name.replace('_', '-') + '/',
                'access': ('history_read' if name == 'offset_list' else
                           'business_read' if name in {'settlement_list', 'settlement_detail',
                                                       'settlement_form', 'aging_list',
                                                       'clearance_list'} else
                           'business_write')}
               for name in ('settlement_list', 'settlement_detail', 'settlement_form', 'settlement_save',
                            'settlement_action', 'aging_list', 'clearance_create',
                            'clearance_reverse', 'clearance_list', 'offset_list',
                            'offset_save')],
}
