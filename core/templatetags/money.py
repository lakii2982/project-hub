"""Display integer cents and minutes without floating-point arithmetic."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from django import template

register = template.Library()


def _decimal(value):
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


@register.filter
def money(value):
    value = _decimal(value)
    if value is None:
        return "—"
    with localcontext() as ctx:
        ctx.prec = max(40, len(value.as_tuple().digits) + 8)
        return format((value / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ",.2f")


@register.filter
def wan(value):
    value = _decimal(value)
    if value is None:
        return "—"
    with localcontext() as ctx:
        ctx.prec = max(40, len(value.as_tuple().digits) + 8)
        return format((value / Decimal(1000000)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ",.2f") + "万"


@register.filter
def hours(value):
    value = _decimal(value)
    if value is None:
        return "—"
    with localcontext() as ctx:
        ctx.prec = max(40, len(value.as_tuple().digits) + 8)
        result = format((value / Decimal(60)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ",.2f")
        return result.rstrip("0").rstrip(".")


@register.filter
def percent(value):
    value = _decimal(value)
    if value is None:
        return "0"
    return str(max(0, min(100, int(value))))


@register.filter
def get_item(value, key):
    """Resolve a CSV column by its header; output remains autoescaped."""
    return value.get(key, "") if isinstance(value, dict) else ""


@register.filter
def document_kind(value):
    return {"expense": "成本费用", "receipt": "实际收款", "payment": "实际付款",
            "baseline": "预算基线", "change": "预算变更", "acceptance": "项目验收"}.get(value, value or "未识别")


@register.filter
def candidate_field(value):
    return {"project_code": "项目编号", "kind": "业务类型", "title": "标题", "amount": "金额",
            "occurred_on": "发生日期", "counterparty": "往来方", "reference": "凭证依据",
            "evidence": "原文证据"}.get(value, value)
