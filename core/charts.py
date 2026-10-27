"""Pure integer/Decimal chart-coordinate helpers.

This module deliberately produces only plain coordinate dictionaries.  SVG
markup, styling and accessibility attributes belong to the templates so that
financial data never has to flow through JavaScript.

Amounts are accepted as integer cents (or integer minutes) and converted to
axis units with ``divisor`` only while computing coordinates.  Floats and
booleans are rejected so no binary floating point can enter money maths.
"""

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

VIEWBOX = "0 0 760 300"
LEFT = Decimal("72")
RIGHT = Decimal("736")
TOP = Decimal("20")
BOTTOM = Decimal("250")
Y_TICK_COUNT = 5
MAX_X_TICKS = 6


def _number(value):
    """Return an exact Decimal, rejecting bool/float and non-finite values."""
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("chart values must be integers or Decimal, never float")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("chart values must be finite")
        return value
    if isinstance(value, int):
        return Decimal(value)
    raise ValueError(f"unsupported chart value: {type(value).__name__}")


def _key(value, index):
    """Return a comparable x value; real dates keep real spacing."""
    if isinstance(value, datetime):
        return value.date().toordinal()
    if isinstance(value, date):
        return value.toordinal()
    return index


def _x_scale(key, low, high):
    if high == low:
        return (LEFT + RIGHT) / Decimal(2)
    span = Decimal(high - low)
    return LEFT + Decimal(key - low) * (RIGHT - LEFT) / span


def _x_label(value, span_days):
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        if span_days > 366:
            return value.strftime("%Y-%m")
        return value.strftime("%m.%d")
    return str(value)


def _compact_yuan(value):
    """Compact axis label; exact cents stay in the caller's data rows."""
    magnitude = abs(value)
    if magnitude >= Decimal("100000000"):
        scaled, suffix = value / Decimal("100000000"), "亿"
    elif magnitude >= Decimal("10000"):
        scaled, suffix = value / Decimal("10000"), "万"
    else:
        scaled, suffix = value, ""
    if abs(scaled) >= 10:
        text = format(scaled.quantize(Decimal("1"), rounding=ROUND_HALF_UP), ",f")
    elif abs(scaled) >= 1:
        text = format(scaled.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP), "f")
    else:
        text = format(scaled.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in ("", "-0"):
        text = "0"
    return f"{text}{suffix}"


def _tick_xs(unique, count=MAX_X_TICKS):
    size = len(unique)
    if size <= count:
        return list(range(size))
    step = Decimal(size - 1) / Decimal(count - 1)
    return sorted({int((step * i).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
                   for i in range(count)})


def line_chart(rows, series, *, x_key="date", divisor=100):
    """Build a pure coordinate dictionary for native SVG rendering.

    ``rows`` are mappings containing ``x_key`` plus one value per series key.
    Values are integer cents (``divisor=100`` turns them into yuan) or integer
    minutes (``divisor=1``).  ``None`` means "not observed yet" and is omitted
    from that series' points.  Dates are spaced by their real day distance.
    """
    rows = list(rows or [])
    series = list(series or [])
    scale = _number(divisor)
    if scale == 0:
        raise ValueError("divisor must not be zero")

    keys = [_key(row.get(x_key), index) for index, row in enumerate(rows)]
    low = min(keys) if keys else 0
    high = max(keys) if keys else 0
    span_days = high - low

    positions = [_x_scale(key, low, high) for key in keys]

    raw_values = []
    for spec in series:
        for row in rows:
            value = row.get(spec["key"])
            if value is not None:
                raw_values.append(_number(value) / scale)
    low_value = min([Decimal(0)] + raw_values)
    high_value = max([Decimal(0)] + raw_values)
    if low_value == high_value:
        padding = max(abs(low_value) * Decimal("0.1"), Decimal("1"))
    else:
        padding = (high_value - low_value) * Decimal("0.05")
    axis_low = low_value - padding
    axis_high = high_value + padding
    axis_span = axis_high - axis_low

    def y_scale(value):
        return TOP + (axis_high - value) * (BOTTOM - TOP) / axis_span

    zero_y = y_scale(Decimal(0))

    y_ticks = []
    for index in range(Y_TICK_COUNT):
        value = axis_high - axis_span * Decimal(index) / Decimal(Y_TICK_COUNT - 1)
        y_ticks.append({"y": f"{y_scale(value):.3f}", "label": _compact_yuan(value)})

    ordered = sorted(range(len(rows)), key=lambda index: keys[index])
    unique_keys = []
    seen = set()
    for index in ordered:
        if keys[index] not in seen:
            seen.add(keys[index])
            unique_keys.append(index)
    x_ticks = []
    for index in _tick_xs(unique_keys):
        row_index = unique_keys[index]
        value = rows[row_index].get(x_key)
        x_ticks.append({
            "x": f"{positions[row_index]:.3f}",
            "label": rows[row_index].get("label") or _x_label(value, span_days),
        })

    chart_series = []
    for spec in series:
        points = []
        dots = []
        for index, row in enumerate(rows):
            value = row.get(spec["key"])
            if value is None:
                continue
            x = positions[index]
            y = y_scale(_number(value) / scale)
            points.append(f"{x:.3f},{y:.3f}")
            dots.append({"cx": f"{x:.3f}", "cy": f"{y:.3f}"})
        chart_series.append({
            "key": spec["key"],
            "label": spec.get("label") or spec["key"],
            "reference": bool(spec.get("reference", False)),
            "points": " ".join(points),
            "dots": dots,
        })

    return {
        "viewbox": VIEWBOX,
        "left": "72",
        "right": "736",
        "top": "20",
        "bottom": "250",
        "zero_y": f"{zero_y:.3f}",
        "y_ticks": y_ticks,
        "x_ticks": x_ticks,
        "series": chart_series,
    }
