"""Editable starting plans, not completed deliverables or inferred business facts."""
from datetime import timedelta
from .models import Milestone
from .services import create_task, audit

BLUEPRINTS = {
    "software": [("需求与范围基线", "确认用户故事、交付边界与验收条件"), ("技术方案与设计", "完成架构和关键界面评审"),
                 ("迭代开发与联调", "功能及接口达到约定测试标准"), ("测试与用户验收", "记录测试结果和客户验收反馈"), ("部署与交接", "交付部署、操作说明及支持约定")],
    "implementation": [("业务调研与差异分析", "确认流程、数据范围与职责"), ("实施蓝图确认", "确认配置、集成及数据迁移方案"),
                       ("配置与数据迁移", "完成配置、接口及迁移核对"), ("培训与上线演练", "完成用户培训和上线演练"), ("上线与稳定支持", "取得上线确认并完成交接")],
    "service": [("服务范围与SLA确认", "确认服务边界、响应方式和验收周期"), ("服务准备", "完成环境和联系人交接"),
                ("服务执行与记录", "保留每期服务记录和问题处理结果"), ("周期服务确认", "确认约定周期内的服务成果"), ("续约或交接", "完成续约评估或服务交接")],
}

def apply_blueprint(member, project):
    rows = BLUEPRINTS[project.template]
    span = (project.end_date - project.start_date).days
    previous = None
    for index, (name, criteria) in enumerate(rows):
        previous = create_task(member, {"project": project, "name": name, "owner": project.manager,
            "start_date": project.start_date + timedelta(days=span * index // len(rows)),
            "due_date": project.start_date + timedelta(days=span * (index + 1) // len(rows)),
            "estimate_minutes": 480, "description": criteria, "dependency": previous})
    for index, name in enumerate(["范围基线确认", "阶段交付确认", "最终验收与交接"]):
        milestone = Milestone.objects.create(project=project, name=name,
            due_date=project.start_date + timedelta(days=span * (index + 1) // 3),
            criteria="按合同或项目约定提交交付物，并保留确认记录。")
        audit(member, "milestone.created_from_template", milestone, {"template": project.template, "project_id": project.pk})
