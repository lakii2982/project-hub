class Plugin:
    manifest = {
        'id': 'costing', 'name': '月度人工', 'version': '0.1.0',
        'kernel_api': '>=1.0,<2', 'requires': {}, 'default_enabled': True,
        'capabilities': [], 'description': '月度人工发生确认、容量核对与守恒分配。',
    }
