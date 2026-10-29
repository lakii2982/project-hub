import csv
import io
"""Customer-facing HTTP forms and financial privacy boundaries."""
import uuid
from django.test import TestCase, Client
from tests.test_acceptance_revenue import RevenueFixture
from pmo_plugins.delivery.models import Acceptance, CustomerConfirmationEvidence


class AcceptanceHTTPTests(RevenueFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.auditor=self.member('auditor'); self.employee_member=self.member('member')

    def login(self, member):self.client.force_login(member.user)

    def data(self, **changes):
        data={'line':str(self.line.pk),'contact':str(self.contact.pk),'confirmed_on':self.today.isoformat(),
              'channel':'客户邮件','reference':'CUSTOMER-EVIDENCE-001','content':'客户确认真实交付范围',
              'request_token':str(uuid.uuid4()),'coverage-TOTAL_FORMS':'1','coverage-INITIAL_FORMS':'0',
              'coverage-MIN_NUM_FORMS':'1','coverage-MAX_NUM_FORMS':'1000','coverage-0-milestone_id':str(self.milestone.pk),
              'coverage-0-from_percent':'0.00','coverage-0-to_percent':'33.33'}
        data.update(changes);return data

    def action(self,member,row,action,**extra):
        self.login(member)
        return self.client.post(f'/modules/delivery/acceptance-action/?id={row.pk}&action={action}',
            {'expected_version':row.version,'request_token':str(uuid.uuid4()),**extra})

    def test_customer_form_partial_price_confirm_revenue_and_reversal(self):
        self.login(self.owner)
        response=self.client.get('/modules/delivery/acceptance-form/',{'line':str(self.line.pk)})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'客户联系人')
        response=self.client.post('/modules/delivery/acceptance-save/',self.data())
        self.assertEqual(response.status_code,302)
        row=Acceptance.objects.get();self.assertEqual(row.accepted_net_cents,34)
        self.assertEqual(self.action(self.owner,row,'submit').status_code,302);row.refresh_from_db()
        self.assertEqual(self.action(self.owner,row,'review',decision='approve',reason='自审拒绝').status_code,403)
        self.assertEqual(self.action(self.finance,row,'review',decision='approve',reason='核对真实客户依据').status_code,302);row.refresh_from_db()
        self.assertEqual(self.action(self.owner,row,'recognize').status_code,302)
        self.login(self.auditor)
        response=self.client.get('/modules/delivery/acceptance-detail/',{'id':str(row.pk)})
        self.assertContains(response,'0.34');self.assertContains(response,'CUSTOMER-EVIDENCE-001')
        self.assertNotContains(response,'&#x27;namespace&#x27;')
        self.assertEqual(self.action(self.finance,row,'reverse',reason='先反验收不允许').status_code,400)
        self.assertEqual(self.action(self.owner,row,'reverse_income',reason='原额完整冲销').status_code,302)
        self.assertEqual(self.action(self.finance,row,'reverse',reason='核对后反验收').status_code,302)
        row.refresh_from_db();self.assertEqual(row.status,'reversed')
        self.login(self.owner)
        response=self.client.get('/modules/delivery/acceptance-form/',{'id':str(row.pk),'action':'replace'})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,str(row.pk))
        self.assertContains(response,'type="hidden" name="replaces_acceptance_id"')
        self.assertContains(response,self.contract.contract_code)
        invalid=self.client.post('/modules/delivery/acceptance-save/',self.data(
            replaces_acceptance_id=str(row.pk),reference='NEW-CUSTOMER-CONFIRMATION',content='客户新依据确认正确范围',
            **{'coverage-0-to_percent':'0.00'}))
        self.assertEqual(invalid.status_code,400)
        self.assertContains(invalid,f'value="{row.pk}"',status_code=400)
        self.assertContains(invalid,'以新客户依据替代原验收',status_code=400)
        self.assertContains(invalid,self.contract.contract_code,status_code=400)
        self.assertContains(invalid,'NEW-CUSTOMER-CONFIRMATION',status_code=400)
        response=self.client.post('/modules/delivery/acceptance-save/',self.data(

            replaces_acceptance_id=str(row.pk),reference='NEW-CUSTOMER-CONFIRMATION',content='客户新依据确认正确范围'))
        self.assertEqual(response.status_code,302)
        replacement=Acceptance.objects.exclude(pk=row.pk).get()
        self.assertEqual(replacement.replaces_acceptance_id,row.pk)
        self.assertEqual(self.action(self.owner,replacement,'submit').status_code,302);replacement.refresh_from_db()
        self.assertEqual(self.action(self.finance,replacement,'review',decision='approve',reason='核对新真实依据').status_code,302);replacement.refresh_from_db()
        self.assertEqual(self.action(self.owner,replacement,'recognize').status_code,302)


    def test_failed_save_rolls_back_evidence_and_preserves_user_inputs(self):
        self.login(self.owner)
        before=CustomerConfirmationEvidence.objects.count()
        response=self.client.post('/modules/delivery/acceptance-save/',self.data(**{'coverage-0-to_percent':'0.00'}))
        self.assertEqual(response.status_code,400)
        self.assertContains(response,'CUSTOMER-EVIDENCE-001',status_code=400)
        self.assertContains(response,'客户确认真实交付范围',status_code=400)
        self.assertEqual(before,CustomerConfirmationEvidence.objects.count())
        # Valid form but already consumed coverage: the service must roll back new evidence.
        self.acceptance(start=0,end=10000)
        before=CustomerConfirmationEvidence.objects.count()
        response=self.client.post('/modules/delivery/acceptance-save/',self.data())
        self.assertEqual(response.status_code,400)
        self.assertEqual(before,CustomerConfirmationEvidence.objects.count())

    def test_roles_and_cross_project_access(self):
        row=self.acceptance()
        for member in (self.owner,self.finance,self.pm,self.auditor):
            self.login(member)
            self.assertEqual(self.client.get('/modules/delivery/acceptance-detail/',{'id':str(row.pk)}).status_code,200)
        self.login(self.employee_member)
        self.assertEqual(self.client.get('/modules/delivery/acceptance-detail/',{'id':str(row.pk)}).status_code,403)
        self.assertEqual(self.client.post('/modules/delivery/acceptance-save/',self.data()).status_code,403)
        for member in (self.auditor,self.pm):
            self.assertEqual(self.action(member,row,'recognize').status_code,403)
        outsider=self.member('pm');self.login(outsider)
        self.assertEqual(self.client.get('/modules/delivery/acceptance-detail/',{'id':str(row.pk)}).status_code,403)

    def test_csrf_and_client_price_injection_have_no_business_side_effect(self):
        self.login(self.owner)
        response=self.client.post('/modules/delivery/acceptance-save/',self.data(accepted_net_cents='1'))
        self.assertEqual(response.status_code,400)
        self.assertFalse(Acceptance.objects.exists())
        self.assertFalse(CustomerConfirmationEvidence.objects.exists())
        protected=Client(enforce_csrf_checks=True)
        protected.force_login(self.owner.user)
        self.assertEqual(protected.post('/modules/delivery/acceptance-save/',self.data()).status_code,403)
        self.assertFalse(Acceptance.objects.exists())
        self.assertFalse(CustomerConfirmationEvidence.objects.exists())

    def test_project_finance_and_csv_original_reversal_sources_and_privacy(self):
        from core.models import ProjectMember, Organization, Membership
        from django.contrib.auth import get_user_model
        row=self.acceptance()
        recognition=self.recognize(row)
        adjustment=self.reverse(recognition)
        ProjectMember.objects.create(project=self.project,member=self.employee_member)
        path=f'/export/management-revenue/?project={self.project.pk}'
        for member in (self.owner,self.finance,self.auditor):
            self.login(member)
            response=self.client.get(path)
            self.assertEqual(response.status_code,200)
            values=list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
            self.assertEqual(len(values),2)
            self.assertEqual(sorted(int(item['管理收入净额_分']) for item in values),[-101,101])
            self.assertEqual(sum(int(item['管理收入净额_分']) for item in values),0)
            self.assertEqual({item['原验收'] for item in values},{str(row.pk)})
            self.assertEqual({item['收入原记录'] for item in values},{str(recognition.pk)})
            self.assertEqual({item['合同批准版本'] for item in values},{str(self.line.contract_version_id)})
            self.assertEqual({item['核算事件'] for item in values},{str(recognition.accounting_event_id),str(adjustment.accounting_event_id)})
            self.assertEqual({item['冲销原核算事件'] for item in values},{'',str(recognition.accounting_event_id)})
            for page in (f'/projects/{self.project.pk}/',f'/finance/?project={self.project.pk}'):
                response=self.client.get(page)
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.context['revenue_summary']['management_revenue_cents'],0)
                self.assertContains(response,'已确认管理收入')
        self.login(self.pm)
        response=self.client.get(f'/projects/{self.project.pk}/')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['revenue_summary']['recognition_count'],1)
        self.assertContains(response,'已确认管理收入')
        self.assertEqual(self.client.get(path).status_code,403)
        self.login(self.employee_member)
        response=self.client.get(f'/projects/{self.project.pk}/')
        self.assertEqual(response.status_code,200)
        self.assertIsNone(response.context['revenue_summary'])
        self.assertNotContains(response,'已确认管理收入')
        self.assertNotContains(response,str(recognition.pk))
        self.assertEqual(self.client.get('/finance/').status_code,403)
        self.assertEqual(self.client.get(path).status_code,403)
        tenant=Organization.objects.create(name='隔离收入租户')
        user=get_user_model().objects.create_user('revenue-tenant-isolated')
        foreign=Membership.objects.create(organization=tenant,user=user,role='owner')
        self.login(foreign)
        self.assertEqual(self.client.get(path).status_code,404)
        self.assertEqual(self.client.get(f'/projects/{self.project.pk}/').status_code,404)
        response=self.client.get('/export/management-revenue/')
        self.assertEqual(response.status_code,200)
        self.assertNotIn(str(recognition.pk).encode(),response.content)
        self.assertEqual(len(list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))),0)
