"""Notification inbox forms; no source/ref/reminder fields are accepted."""
from django import forms
from django.core.exceptions import ValidationError


class NotificationFilterForm(forms.Form):
    status = forms.ChoiceField(label='状态', required=False, choices=[
        ('', '未忽略'), ('unread', '未读'), ('read', '已读'),
        ('ignored', '已忽略'), ('all', '全部历史')])


class NotificationActionForm(forms.Form):
    def clean(self):
        data = super().clean()
        if set(self.data) - {'csrfmiddlewaretoken'}:
            raise ValidationError('包含不允许的字段，请刷新后重试')
        return data
