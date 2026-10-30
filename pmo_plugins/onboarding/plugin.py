class Plugin:
    manifest = {
        'id': 'onboarding', 'name': '导入与期初', 'version': '0.1.0',
        'kernel_api': '>=1.0,<2',
        'requires': {'contracts': '>=0.1.0', 'settlement': '>=0.1.0',
                     'cash': '>=0.1.0'},
        'default_enabled': True, 'capabilities': [],
        'description': '导入预览（只读模板识别/字段映射/行级预检）与后续期初编排。',
    }
