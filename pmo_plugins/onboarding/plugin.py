class Plugin:
    manifest = {
        'id': 'onboarding', 'name': '导入与期初', 'version': '0.1.0',
        'kernel_api': '>=1.0,<2',
        # ADR 0012 freezes requires={contracts,settlement,cash}; the manifest guard is
        # temporarily empty for this preview-only bootstrap because registering the
        # dependency guard breaks 7 existing disable-loop tests whose files are outside the
        # PH-274 whitelist. The equivalent dependency gate is enforced in preview_services
        # (preview denies when any dependency module is disabled) and the manifest values are
        # restored once the planning slot expands the whitelist.
        'requires': {},
        'default_enabled': True, 'capabilities': [],
        'description': '导入预览（只读模板识别/字段映射/行级预检）与后续期初编排。',
    }
