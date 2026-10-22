from __future__ import annotations

import pluggy

hookimpl = pluggy.HookimplMarker("project_hub")


def _alert(
    severity: str, title: str, detail: str, url: str
) -> dict[str, str]:
    return {
        "severity": severity,
        "title": title[:160],
        "detail": detail[:1000],
        "url": url,
    }


class Plugin:
    manifest = {
        "id": "project_health",
        "name": "项目健康",
        "version": "0.1.0",
        "kernel_api": ">=1.0,<2",
        "requires": {},
        "default_enabled": True,
        "capabilities": ["project:read_snapshot", "insights:emit"],
        "description": "根据项目快照生成预算、任务、里程碑和计划提醒。",
    }

    @hookimpl
    def project_insights(
        self, snapshot: dict, include_finance: bool
    ) -> list[dict[str, str]]:
        alerts: list[dict[str, str]] = []
        project_id = snapshot["id"]
        name = str(snapshot.get("name") or project_id)
        stage = snapshot.get("stage")
        base_url = snapshot["url"]
        tasks_url = f"{base_url}#tasks"
        milestones_url = f"{base_url}#milestones"
        finance_url = f"{base_url}#finance"

        if include_finance:
            budget = snapshot.get("budget_cents")
            eac = snapshot.get("eac_cents")
            if (
                snapshot.get("budget_approved")
                and budget is not None
                and eac is not None
                and eac > budget
            ):
                alerts.append(
                    _alert(
                        "high",
                        f"{name}预计超出预算",
                        "已发生成本加预计剩余成本超过已批准预算，请核对剩余工作或提交变更。",
                        finance_url,
                    )
                )

        overdue_tasks = snapshot.get("overdue_tasks") or 0
        if overdue_tasks:
            alerts.append(
                _alert(
                    "high",
                    f"{name}有{overdue_tasks}项任务逾期",
                    "请核对前置依赖、责任人与交付日期，记录处理结果。",
                    tasks_url,
                )
            )

        for milestone in snapshot.get("overdue_milestones") or []:
            milestone_name = milestone.get("name") or "未命名"
            due_date = milestone.get("due_date") or "未记录"
            alerts.append(
                _alert(
                    "high",
                    f"里程碑逾期：{milestone_name}",
                    f"计划验收日{due_date}，尚未记录验收证据。",
                    milestones_url,
                )
            )

        if include_finance:
            if (
                not snapshot.get("budget_approved")
                and stage not in {"closed", "cancelled"}
            ):
                alerts.append(
                    _alert(
                        "medium",
                        f"{name}尚未审批预算基线",
                        "先确认预算与成本费率，再启动执行。",
                        finance_url,
                    )
                )

            for plan in snapshot.get("overdue_plans") or []:
                kind = "回款" if plan.get("direction") == "in" else "付款"
                severity = "high" if kind == "回款" else "medium"
                title = plan.get("title") or "未命名"
                due_date = plan.get("due_date") or "未记录"
                alerts.append(
                    _alert(
                        severity,
                        f"{kind}逾期：{title}",
                        f"到期日{due_date}。请补记已发生流水，或核对交易对方与履约条件。",
                        finance_url,
                    )
                )

            etc_cents = snapshot.get("etc_cents")
            if (
                stage not in {"closed", "cancelled"}
                and snapshot.get("has_open_tasks")
                and etc_cents == 0
            ):
                alerts.append(
                    _alert(
                        "medium",
                        f"{name}未估算剩余成本",
                        "尚有未完成任务，预计剩余成本为0，完工成本预测可能偏低。",
                        base_url,
                    )
                )

        return alerts

    @hookimpl
    def event_received(self, event: dict) -> dict[str, str]:
        return {"message": "业务事件已检查", "topic": event["topic"]}
