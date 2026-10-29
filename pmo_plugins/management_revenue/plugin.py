class Plugin:
    manifest = {
        'id': 'management_revenue', 'name': '管理收入', 'version': '0.1.0',
        'kernel_api': '>=1.0,<2', 'requires': {'contracts': '>=0.1.0', 'delivery': '>=0.1.0'},
        'default_enabled': True, 'capabilities': [],
        'description': '按独立客户验收确认管理收入及原分冲销。',
    }
