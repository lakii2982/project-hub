DOMAIN_METADATA = {
    'schema_version':'1','plugin_id':'delivery',
    'django_app':{'module':'pmo_plugins.delivery','label':'delivery'},
    'contracts':{'commands':[],'queries':[],'events':[]},
    'migration_dependencies':['core','contracts'],
    'routes':[{'name':name,'path':name.replace('_','-')+'/','access':'business_read' if name in {'acceptance_list','acceptance_detail','acceptance_form','service_confirmation_list','service_confirmation_detail','service_confirmation_form'} else 'business_write'} for name in ('acceptance_list','acceptance_detail','acceptance_form','acceptance_save','acceptance_action','service_confirmation_list','service_confirmation_detail','service_confirmation_form','service_confirmation_save','service_confirmation_action')] + [{'name':'ops_reconciliation_overview','path':'ops-reconciliation-overview/','access':'history_read'}],
}
