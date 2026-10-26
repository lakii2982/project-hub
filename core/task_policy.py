"""Key-task link rules shared by create_task and edit_task.

A new or edited plan is a key task only when it is tied to a verifiable
milestone or an active cash plan.  Historical tasks keep their null links and
remain readable/updatable; this module never guesses links for them.
"""
from django.core.exceptions import ValidationError
from .models import Milestone, CashPlan


def validate_key_task(project, data):
    """Validate and return ``(milestone, cash_plan)`` for a key task plan.

    Both links are optional individually, but at least one is required.
    Links must belong to the same project.  A cash plan must still be active.
    When both links are supplied, a cash plan that already names a milestone
    must name the same milestone as the task.
    """
    milestone = data.get("milestone")
    cash_plan = data.get("cash_plan")
    if not milestone and not cash_plan:
        raise ValidationError("关键任务须至少关联一个同项目里程碑或有效现金计划")
    if milestone:
        milestone = Milestone.objects.filter(pk=milestone.pk, project=project).first()
        if milestone is None:
            raise ValidationError("任务关联的里程碑须属于同一项目")
    if cash_plan:
        cash_plan = CashPlan.objects.select_related("milestone").filter(pk=cash_plan.pk, project=project).first()
        if cash_plan is None:
            raise ValidationError("任务关联的现金计划须属于同一项目")
        if not cash_plan.active:
            raise ValidationError("任务关联的现金计划已停用，请选择有效计划")
        if cash_plan.milestone_id and cash_plan.milestone.project_id != project.pk:
            raise ValidationError("现金计划关联的里程碑须属于同一项目")
        if cash_plan.milestone_id and milestone and cash_plan.milestone_id != milestone.pk:
            raise ValidationError("现金计划关联的里程碑与任务所选里程碑不一致")
    return milestone, cash_plan
