from __future__ import annotations

import pluggy

hookimpl = pluggy.HookimplMarker("project_hub")


class Plugin:
    manifest = {
        "id": "delivery_watch",
        "name": "交付巡检",
        "version": "0.1.0",
        "kernel_api": ">=1.0,<2",
        "requires": {"project_health": ">=0.1,<1"},
        "default_enabled": False,
        "capabilities": ["project:read_snapshot", "insights:emit"],
        "description": "为七天内到期的项目里程碑生成交付提醒。",
    }

    @hookimpl
    def project_insights(
        self, snapshot: dict, include_finance: bool
    ) -> list[dict[str, str]]:
        alerts: list[dict[str, str]] = []
        base_url = snapshot["url"]
        for milestone in snapshot.get("upcoming_milestones") or []:
            days = milestone.get("days_remaining")
            if not isinstance(days, int) or not 0 <= days <= 7:
                continue
            name = milestone.get("name") or "未命名"
            due_date = milestone.get("due_date") or "未记录"
            alerts.append(
                {
                    "severity": "medium",
                    "title": f"里程碑即将到期：{name}"[:160],
                    "detail": (
                        f"计划验收日{due_date}，剩余{days}天，请核对交付与验收证据。"
                    )[:1000],
                    "url": f"{base_url}#milestones",
                }
            )
        return alerts
