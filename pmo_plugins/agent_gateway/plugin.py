class Plugin:
    manifest = {
        "id": "agent_gateway",
        "name": "个人代理网关",
        "version": "0.1.0",
        "kernel_api": ">=1.0,<2",
        "requires": {},
        "default_enabled": True,
        "capabilities": ["personal_agent_api"],
        "description": "连接个人办公助手，在授权项目内查询和填报信息。",
    }
