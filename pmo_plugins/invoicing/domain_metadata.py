DOMAIN_METADATA = {
    'schema_version': '1', 'plugin_id': 'invoicing',
    'django_app': {'module': 'pmo_plugins.invoicing', 'label': 'invoicing'},
    'contracts': {'commands': [], 'queries': [], 'events': []},
    'migration_dependencies': ['core', 'contracts', 'settlement'],
    'routes': [
        {'name': 'invoice_list', 'path': 'invoice-list/', 'access': 'history_read'},
        {'name': 'invoice_form', 'path': 'invoice-form/', 'access': 'business_read'},
        {'name': 'invoice_record', 'path': 'invoice-record/', 'access': 'business_write'},
        {'name': 'invoice_detail', 'path': 'invoice-detail/', 'access': 'history_read'},
        {'name': 'invoice_allocate', 'path': 'invoice-allocate/', 'access': 'business_write'},
        {'name': 'invoice_reverse', 'path': 'invoice-reverse/', 'access': 'business_write'},
        {'name': 'invoice_tax', 'path': 'invoice-tax/', 'access': 'history_read'},
        {'name': 'invoice_tax_confirm', 'path': 'invoice-tax-confirm/', 'access': 'business_write'},
        {'name': 'invoice_tax_link', 'path': 'invoice-tax-link/', 'access': 'business_write'},
    ],
}
