DOMAIN_METADATA = {
    'schema_version': '1', 'plugin_id': 'cash',
    'django_app': {'module': 'pmo_plugins.cash', 'label': 'cash'},
    'contracts': {'commands': [], 'queries': [], 'events': []},
    'migration_dependencies': ['core'],
    'routes': [{'name': name, 'path': name.replace('_', '-') + '/',
                'access': 'business_read' if name in {'funding_account_list', 'funding_account_form', 'cash_transaction_list', 'cash_transaction_form', 'cash_transaction_detail'} else 'business_write'}
               for name in ('funding_account_list', 'funding_account_form', 'funding_account_save',
                            'funding_account_activate', 'cash_transaction_list', 'cash_transaction_form',
                            'cash_transaction_save', 'cash_transaction_detail', 'cash_party_match',
                            'cash_transaction_record')],
}