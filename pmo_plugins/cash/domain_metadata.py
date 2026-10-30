DOMAIN_METADATA = {
    'schema_version': '1', 'plugin_id': 'cash',
    'django_app': {'module': 'pmo_plugins.cash', 'label': 'cash'},
    'contracts': {'commands': [], 'queries': [], 'events': []},
    'migration_dependencies': ['core'],
    'routes': [{'name': name, 'path': name.replace('_', '-') + '/',
                'access': 'history_read' if name in {'cash_bridge_snapshot', 'cash_bridge_issues', 'bank_balance_snapshot', 'bank_balance_issues'}
                          else ('business_read' if name in {'funding_account_list', 'funding_account_form', 'cash_transaction_list', 'cash_transaction_form', 'cash_transaction_detail', 'cash_refund_list', 'cash_refund_form', 'cash_bank_return_form', 'cash_classification_list', 'cash_classification_form', 'cash_classification_reverse', 'cash_internal_transfer_list', 'cash_internal_transfer_form', 'cash_internal_transfer_reverse'} else 'business_write')}
               for name in ('funding_account_list', 'funding_account_form', 'funding_account_save',
                            'funding_account_activate', 'cash_transaction_list', 'cash_transaction_form',
                            'cash_transaction_save', 'cash_transaction_detail', 'cash_party_match',
                            'cash_transaction_record', 'cash_refund_list', 'cash_refund_form',
                            'cash_refund_save', 'cash_bank_return_form',
                            'cash_bank_return_save', 'cash_classification_list', 'cash_classification_form',
                            'cash_classification_save', 'cash_classification_reverse',
                            'cash_classification_reverse_save', 'cash_internal_transfer_list',
                            'cash_internal_transfer_form', 'cash_internal_transfer_save',
                            'cash_internal_transfer_reverse',
                            'cash_internal_transfer_reverse_save', 'cash_bridge_snapshot', 'cash_bridge_issues', 'bank_balance_snapshot', 'bank_balance_issues')],
}