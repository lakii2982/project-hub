class Plugin:
    manifest = {
        'id': 'cash', 'name': '现金事实', 'version': '0.1.0',
        'kernel_api': '>=1.0,<2',
        'requires': {},
        'default_enabled': True, 'capabilities': [],
        'description': '银行账户、银行流水事实与往来方匹配（无过账）。',
    }
