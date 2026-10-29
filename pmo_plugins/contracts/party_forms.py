import uuid

from django import forms
from django.utils import timezone

from .models import Counterparty, normalize_display, normalized_roles


class StrictForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not isinstance(field.widget, forms.HiddenInput):
                field.widget.attrs.setdefault("class", "form-control")

    def clean(self):
        data = super().clean()
        unknown = set(self.data) - set(self.fields) - {"csrfmiddlewaretoken"}
        if unknown:
            raise forms.ValidationError("表单包含不允许的字段，请刷新后重试")
        return data


class PartyForm(StrictForm):
    party_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    display_name = forms.CharField(label="客户／供应商名称", max_length=160)
    roles = forms.MultipleChoiceField(
        label="业务角色",
        choices=[("customer", "客户"), ("supplier", "供应商")],
        widget=forms.CheckboxSelectMultiple,
        help_text="同一记录可同时作为客户和供应商，请勿重复建档。",
    )
    active_from = forms.DateField(label="有效起始日", widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
    inactive_on = forms.DateField(
        label="失效日期（可选）",
        required=False,
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
        help_text="当前仅维护有效档案；合作状态调整请联系负责人。",
    )
    request_token = forms.UUIDField(required=False, widget=forms.HiddenInput)
    expected_version = forms.IntegerField(required=False, min_value=1, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["active_from"].initial = timezone.localdate()
        if not self.is_bound:
            self.fields["request_token"].initial = uuid.uuid4()

    def clean_display_name(self):
        return normalize_display(self.cleaned_data["display_name"])

    def clean_roles(self):
        return normalized_roles(self.cleaned_data["roles"])

    def clean(self):
        data = super().clean()
        if data.get("active_from") and data.get("inactive_on") and data["inactive_on"] < data["active_from"]:
            self.add_error("inactive_on", "失效日期不能早于有效起始日")
        return data


class ContactForm(StrictForm):
    party_id = forms.UUIDField(widget=forms.HiddenInput)
    contact_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    name = forms.CharField(label="联系人姓名", max_length=100)
    email = forms.EmailField(label="电子邮箱（可选）", required=False)
    phone = forms.CharField(label="联系电话（可选）", max_length=40, required=False)
    request_token = forms.UUIDField(required=False, widget=forms.HiddenInput)
    expected_version = forms.IntegerField(required=False, min_value=1, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.fields["request_token"].initial = uuid.uuid4()

    def clean_name(self):
        return normalize_display(self.cleaned_data["name"])

    def clean_email(self):
        return self.cleaned_data.get("email", "").strip()

    def clean_phone(self):
        return self.cleaned_data.get("phone", "").strip()
