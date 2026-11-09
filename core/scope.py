"""1.0 小微版入口范围：导航白名单与直接 URL 停用门控。

本模块只读配置、不访问数据库。隐藏/停用不等于删除：复杂插件的表、迁移、
历史账与审计全部保留，只收口产品入口（docs/product-scope.md §6、§8.5）。
专业能力回归在 ``PMO_SCOPE=full`` 下保持原样，微版门控由测试显式覆盖。
"""
from django.conf import settings
from django.urls import reverse

MICRO_SCOPE = "micro"
VALID_PRODUCT_SCOPES = frozenset({"micro", "full"})
MICRO_ENABLED_PLUGINS = frozenset()


def resolve_scope(value, testing=False):
    """Resolve PMO_SCOPE fail-closed: only exact ``micro``/``full`` are trusted.

    Unset means the 1.0 micro default. Empty/typo values must never open the
    professional UI, so anything else falls back to ``micro``. The test
    command runs the retained professional regression suite in ``full`` unless
    the caller sets an explicit valid value.
    """
    if value is None:
        return "full" if testing else MICRO_SCOPE
    return value if value in VALID_PRODUCT_SCOPES else MICRO_SCOPE

# 1.0 保留入口（docs/product-scope.md §8.1）。客户与合同/费用/开票与收款/现金
# 的页面由 Q-36/Q-37/Q-38 交付后加入白名单；未交付前不渲染死链。
# Q-36 已交付费用（/expenses/），Q-37 已交付开票与收款（/billing/），
# Q-38 已交付现金（/cash/）；客户与合同随 Q-39/Q-40 后续加入。
MICRO_NAV = (
    {"key": "dashboard", "label": "工作台", "url_name": "dashboard", "icon": "grid",
     "roles": frozenset({"owner", "pm", "member", "finance", "auditor"})},
    {"key": "projects", "label": "项目", "url_name": "projects", "icon": "projects",
     "roles": frozenset({"owner", "pm", "member", "finance", "auditor"})},
    {"key": "time", "label": "工时", "url_name": "timesheets", "icon": "clock",
     "roles": frozenset({"owner", "pm", "member", "finance", "auditor"})},
    {"key": "expenses", "label": "费用", "url_name": "expenses", "icon": "finance",
     "roles": frozenset({"owner", "pm"})},
    {"key": "cashflow", "label": "现金流", "url_name": "cashflow", "icon": "finance",
     "roles": frozenset({"owner", "pm"})},
    {"key": "settings", "label": "设置", "url_name": "settings", "icon": "settings",
     "roles": frozenset({"owner"})},
    {"key": "status", "label": "系统状态", "url_name": "system-status", "icon": "settings",
     "roles": frozenset({"owner", "finance", "auditor"})},
)

# 移出 1.0 的页面：导航不出现，直接 URL 返回停用页而不是继续可读（§8.5）。
MICRO_DISABLED_PREFIXES = (
    "/finance/",
    "/modules/",
    "/approvals/",
    "/assistant/",
    "/reminders/",
    "/notifications/",
    "/plugins/",
    "/connections/",
    "/review/",
    "/reverse/",
    "/export/",
    "/import/invoicing/",
)


def product_scope():
    return getattr(settings, "PMO_SCOPE", MICRO_SCOPE)


def is_micro_scope():
    # Fail closed: only the exact full scope opens the professional capability set.
    return product_scope() != "full"


def is_micro_disabled_path(path):
    return any(path.startswith(prefix) for prefix in MICRO_DISABLED_PREFIXES)


def micro_navigation(member):
    """Return the role-filtered kept navigation for the current member."""
    if not is_micro_scope() or member is None:
        return []
    return [
        {"key": entry["key"], "label": entry["label"],
         "url": reverse(entry["url_name"]), "icon": entry["icon"]}
        for entry in MICRO_NAV if member.role in entry["roles"]
    ]
