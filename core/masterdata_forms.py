from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q

from .forms import DATE, EmployeeForm, ProjectEditForm
from .models import Membership


class EmployeeEditForm(EmployeeForm):
    active = forms.BooleanField(label="在职", required=False)
    capacity_hours = forms.DecimalField(
        label="每日可用工时（小时）",
        min_value=0,
        max_value=24,
        decimal_places=None,
        widget=forms.NumberInput(attrs={"min": 0, "max": 24, "step": "any"}),
    )

    def __init__(self, *args, employee=None, **kwargs):
        self.employee = employee
        super().__init__(*args, **kwargs)
        available = Q(employee__isnull=True)
        if employee is not None and employee.member_id:
            available |= Q(pk=employee.member_id)
        self.fields["member"].queryset = Membership.objects.filter(
            organization=self.member.organization,
            active=True,
            user__is_active=True,
        ).filter(available).select_related("user")

    def clean_capacity_hours(self):
        value = self.cleaned_data["capacity_hours"]
        raw = self.data.get(self.add_prefix("capacity_hours")) if self.is_bound else value
        if isinstance(raw, float):
            raise ValidationError("容量须使用十进制数，不能使用浮点数")
        if not value.is_finite():
            raise ValidationError("容量须为有效数字，不能是无穷或非数")
        minutes = value * Decimal(60)
        if minutes != minutes.to_integral_value():
            raise ValidationError("容量按整数分钟维护，请换算成整分钟")
        if not 1 <= minutes <= 1440:
            raise ValidationError("每日容量须在1到1440分钟之间")
        return value


class ProjectMasterDataForm(ProjectEditForm):
    name = forms.CharField(label="项目名称", max_length=120)
    client = forms.CharField(label="客户", max_length=120)
    manager = forms.ModelChoiceField(
        queryset=Membership.objects.none(), label="项目负责人",
    )
    start_date = forms.DateField(
        label="开始日期", widget=forms.DateInput(attrs=DATE),
    )
    version = forms.IntegerField(widget=forms.HiddenInput, min_value=1)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in (
            "scope", "acceptance_criteria", "end_date", "contract_reference",
        ):
            self.fields[field_name].disabled = False
            self.fields[field_name].help_text = (
                "变更保留审计依据，不自动调整里程碑、关键任务或已批准预算"
            )
        managers = Membership.objects.filter(
            organization=self.member.organization,
            active=True,
            user__is_active=True,
            role__in=["owner", "pm"],
        ).select_related("user")
        if self.member.role == "pm":
            managers = managers.filter(pk=self.member.pk)
        self.fields["manager"].queryset = managers

    def clean(self):
        data = super().clean()
        start_date = data.get("start_date")
        end_date = data.get("end_date")
        if start_date and end_date and end_date < start_date:
            self.add_error("end_date", "交付日期不能早于开始日期")
        return data
