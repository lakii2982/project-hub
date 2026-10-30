class Plugin:
    manifest = {
        'id': 'time', 'name': '工时与可计费', 'version': '0.1.0',
        'kernel_api': '>=1.0,<2', 'requires': {'contracts': '>=0.1.0'},
        'default_enabled': True, 'capabilities': [],
        'description': '记录客户确认的可计费分钟，不写收入、账本或未清。',
    }
