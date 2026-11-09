"""Q-53 视觉一致性回归：共享字段行卡 markup 与系统状态导航高亮。

只断言服务端渲染的结构契约，不重复浏览器布局检查（元素位置与媒体查询由
``docs/engineering/q53-verify-visual.py`` 的 Chromium 检查覆盖）：

- 设置/审计的通用表格带 ``table-rows`` 与每格 ``data-label``，删掉任一属性都会失败；
- 到期提醒表末列保留 ``data-label="来源"``（窄屏单列操作格，不能被通用
  ``grid-column`` 规则挤出隐式窄列）；
- ``/status/`` 的导航上下文为 ``status``，侧栏高亮「系统状态」而不是「设置」。
"""
from datetime import date

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from core.models import CostRate, Employee, Membership, Organization
from core.services import audit


class Q53VisualConsistencyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="Q53 视觉一致性测试公司")
        user_model = get_user_model()
        cls.owner_user = user_model.objects.create_user("q53-owner")
        cls.owner = Membership.objects.create(
            organization=cls.org, user=cls.owner_user, role="owner")
        cls.employee = Employee.objects.create(
            organization=cls.org, name="Q53 员工", title="工程师", capacity_minutes=480)
        cls.rate = CostRate.objects.create(
            employee=cls.employee, effective_from=date(2024, 1, 1),
            hourly_cents=12_345, basis="Q53 费率依据")

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.owner_user)
        audit(self.owner, "q53.visual.test", self.org, {"note": "Q53"})

    def test_settings_generic_tables_render_row_card_labels(self):
        page = self.client.get(reverse("settings"))
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertIn('<table class="table-rows">', html)
        for label in ("岗位", "关联账户", "日可用工时", "用户名", "角色", "项目范围", "版本"):
            self.assertIn(f'data-label="{label}"', html)

    def test_audit_table_renders_row_card_labels(self):
        page = self.client.get(reverse("settings"), {"tab": "audit"})
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertIn('<table class="table-rows">', html)
        for label in ("时间", "操作人", "操作", "业务对象", "对象编号"):
            self.assertIn(f'data-label="{label}"', html)

    def test_due_template_keeps_source_label_and_row_card_class(self):
        # 到期提醒表只有在存在到期事项时才渲染数据行，演示库/测试库常为空；
        # 这里直接断言模板契约，防止后续删除 data-label 或行卡类名。
        from django.template.loader import get_template
        source = get_template("due.html").template.source
        self.assertIn('class="q41-due-table"', source)
        self.assertIn('data-label="来源"', source)

    def test_system_status_highlights_status_navigation(self):
        page = self.client.get(reverse("system-status"))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["nav"], "status")
        html = page.content.decode()
        marker = 'aria-current="page"'
        index = html.index(marker)
        anchor_start = html.rindex("<a ", 0, index)
        anchor = html[anchor_start:index]
        self.assertIn("系统状态", html[index:index + 120])
        self.assertIn("系统状态", html[anchor_start:index + 120])
