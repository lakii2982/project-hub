"""Editable starting plans, not completed deliverables or inferred business facts.

The template creates only verifiable milestones and one key task per milestone.
It never creates day-to-day execution tasks, cash plans or milestone acceptance.
"""
from datetime import timedelta
from .models import Milestone
from .services import create_task, audit

BLUEPRINTS = {
    "software": [("范围基线确认", "确认合同交付边界及验收依据"),
                 ("版本交付验收", "保留阶段交付物与验收确认记录"),
                 ("最终验收与交接", "按合同确认最终交付物、操作文档及支持约定")],
    "implementation": [("实施蓝图确认", "确认实施范围与集成验收依据"),
                       ("上线验收", "保留上线业务验收确认记录"),
                       ("实施交接确认", "交付运维资料并保留最终确认依据")],
    "service": [("服务范围与SLA确认", "确认服务边界与周期验收条件"),
                ("周期服务验收", "按约定周期保留服务成果确认依据"),
                ("服务交接确认", "确认服务期交付与后续支持边界")],
}

def apply_blueprint(member, project):
    rows = BLUEPRINTS[project.template]
    span = (project.end_date - project.start_date).days
    previous = None
    for index, (name, criteria) in enumerate(rows):
        start_date = project.start_date + timedelta(days=span * index // len(rows))
        due_date = project.start_date + timedelta(days=span * (index + 1) // len(rows))
        milestone = Milestone.objects.create(project=project, name=name, due_date=due_date, criteria=criteria)
        audit(member, "milestone.created_from_template", milestone, {"template": project.template, "project_id": project.pk})
        previous = create_task(member, {
            "project": project,
            "name": f"{name}资料确认",
            "owner": project.manager,
            "start_date": start_date,
            "due_date": due_date,
            "description": f"核对并归档「{name}」的验收证据；验收条件：{criteria}",
            "milestone": milestone,
            "dependency": previous,
        })
