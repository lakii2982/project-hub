class Plugin:
    manifest = {
        'id': 'settlement', 'name': '应收结算', 'version': '0.1.0',
        'kernel_api': '>=1.0,<2',
        'requires': {'contracts': '>=0.1.0', 'delivery': '>=0.1.0'},
        'default_enabled': True, 'capabilities': [],
        'description': '客户结算、到期与质保未清及只读账龄。',
    }
