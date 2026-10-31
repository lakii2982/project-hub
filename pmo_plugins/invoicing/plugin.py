class Plugin:
    manifest = {
        'id': 'invoicing', 'name': '发票', 'version': '0.1.0',
        'kernel_api': '>=1.0,<2',
        'requires': {'contracts': '>=0.1.0', 'settlement': '>=0.1.0'},
        'default_enabled': True, 'capabilities': [],
        'description': '销售/采购发票登记、行项目与业务来源分配、红冲（不写收入/现金/账本）。',
    }
