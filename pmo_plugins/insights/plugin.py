class Plugin:
    manifest = {
        'id': 'insights', 'name': '洞察与核对', 'version': '0.1.0',
        'kernel_api': '>=1.0,<2',
        'requires': {'cash': '>=0.1.0', 'settlement': '>=0.1.0',
                     'costing': '>=0.1.0', 'contracts': '>=0.1.0'},
        'default_enabled': True, 'capabilities': [],
        'description': '只读期间预检与跨域核对读模型聚合；不写业务事实，不建第二套账。',
    }
