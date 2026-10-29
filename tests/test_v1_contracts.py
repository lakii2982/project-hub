"""PH-212 business service and production browser boundary regressions."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event, Thread
import time
from html.parser import HTMLParser
from unittest import mock, skipUnless

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection, transaction
from django.test import Client, TestCase, TransactionTestCase
from django.utils import timezone

from core.models import Organization, Membership, Project, PluginState, AuditEvent, DomainEvent, LedgerEntry
from pmo_plugins.contracts.models import Counterparty, Contact, Contract
from pmo_plugins.contracts.party_services import (create_party, update_party, create_contact,
    update_contact, get_party, list_parties)
from pmo_plugins.contracts.services import create_contract_draft, update_contract_draft, get_contract, list_contracts


class Fixture:
    def setUp(self):
        self.today = timezone.localdate()
        self.org = Organization.objects.create(name='演练公司')
        self.owner = self.member('owner')
        self.pm = self.member('pm')
        self.project = Project.objects.create(organization=self.org, code='P1', name='演练项目',
            client='旧文本', manager=self.pm, start_date=self.today, end_date=self.today)
        self.party = self.make_party()

    def member(self, role):
        user = get_user_model().objects.create_user(username=role + uuid.uuid4().hex)
        return Membership.objects.create(organization=self.org, user=user, role=role)

    def make_party(self, **changes):
        values = dict(display_name='示例客户', roles=['customer'], active_from=self.today, request_token=uuid.uuid4())
        values.update(changes)
        return create_party(self.owner, **values)

    def make_contract(self, **changes):
        values = dict(project_id=self.project.pk, party_id=self.party.pk, contract_code='C1', request_token=uuid.uuid4())
        values.update(changes)
        return create_contract_draft(self.owner, **values)

    def snapshot(self):
        return tuple(model.objects.count() for model in (Counterparty, Contact, Contract, AuditEvent, DomainEvent, LedgerEntry))

    def rejects(self, call, exceptions=(ValidationError, PermissionDenied, TypeError)):
        before = self.snapshot()
        with self.assertRaises(exceptions):
            call()
        self.assertEqual(before, self.snapshot())


class ContractsBusinessTests(Fixture, TestCase):
    def test_normalization_dual_roles_and_create_replay(self):
        token = uuid.uuid4()
        party = self.make_party(display_name='  ＡＣＭＥ   Corp ', roles=['supplier', 'customer', 'customer'], request_token=token)
        replay = self.make_party(display_name='acme corp', roles=['customer', 'supplier'], request_token=token)
        self.assertEqual(party.pk, replay.pk)
        self.assertEqual(party.roles, ['customer', 'supplier'])
        self.rejects(lambda: self.make_party(display_name='Acme Corp'))
        self.rejects(lambda: self.make_party(display_name='Another', request_token=token))

    def test_contact_same_names_allowed_and_versioned(self):
        other = self.make_party(display_name='另一客户')
        token = uuid.uuid4()
        contact = create_contact(self.owner, party_id=self.party.pk, name='张三', request_token=token)
        replay = create_contact(self.owner, party_id=self.party.pk.hex, name='张三', request_token=token)
        self.assertEqual(contact.pk, replay.pk)
        create_contact(self.owner, party_id=other.pk, name='张三', request_token=uuid.uuid4())
        update_contact(self.owner, contact.pk, name='张三', email='demo@example.com', expected_version=1)
        self.rejects(lambda: update_contact(self.owner, contact.pk, name='旧表单', expected_version=1))
        self.rejects(lambda: create_contact(self.owner, party_id=self.party.pk, name='坏邮箱', email='bad', request_token=uuid.uuid4()))

    def test_contract_replay_versions_and_no_financial_effect(self):
        token = uuid.uuid4()
        contract = self.make_contract(request_token=token)
        replay = self.make_contract(request_token=token, party_id=self.party.pk.hex, project_id=str(self.project.pk))
        self.assertEqual(contract.pk, replay.pk)
        self.rejects(lambda: self.make_contract(request_token=token, contract_code='C2'))
        update_contract_draft(self.owner, contract.pk, project_id=self.project.pk, party_id=self.party.pk,
            contract_code='C2', expected_version=1)
        self.rejects(lambda: update_contract_draft(self.owner, contract.pk, project_id=self.project.pk,
            party_id=self.party.pk, contract_code='C3', expected_version=1))
        contract.refresh_from_db()
        self.assertEqual((contract.status, contract.version, contract.parent_ref), ('draft', 2, None))
        self.project.refresh_from_db()
        self.assertEqual(self.project.client, '旧文本')
        self.assertEqual(self.project.contract_cents, 0)
        self.assertFalse(LedgerEntry.objects.exists())
        self.assertEqual(AuditEvent.objects.count(), DomainEvent.objects.count())

    def test_party_update_and_unknown_fields(self):
        update_party(self.owner, self.party.pk, display_name='新名', roles=['customer', 'supplier'], active_from=self.today, expected_version=1)
        self.rejects(lambda: update_party(self.owner, self.party.pk, display_name='旧名', roles=['customer'], active_from=self.today, expected_version=1))
        self.rejects(lambda: self.make_party(status='inactive'))
        self.rejects(lambda: self.make_contract(approved_by=self.owner.pk))
        self.rejects(lambda: self.make_party(roles=[{}]))

    def test_refusal_boundaries_are_atomic(self):
        other = Organization.objects.create(name='其他公司')
        foreign = Counterparty.objects.create(organization=other, display_name='隐私', roles=['customer'], active_from=self.today)
        self.rejects(lambda: self.make_contract(party_id=foreign.pk))
        foreign_project = Project.objects.create(organization=other, code='F1', name='隐私项目', manager=self.owner,
            start_date=self.today, end_date=self.today)
        self.rejects(lambda: self.make_contract(project_id=foreign_project.pk))
        self.rejects(lambda: self.make_contract(signed_on=self.today + timedelta(days=1)))
        supplier = self.make_party(display_name='供应商', roles=['supplier'])
        self.rejects(lambda: self.make_contract(party_id=supplier.pk))
        Counterparty.objects.filter(pk=self.party.pk).update(status='inactive')
        self.rejects(lambda: self.make_contract())
        self.rejects(lambda: create_contact(self.owner, party_id=self.party.pk, name='姓名', request_token=uuid.uuid4()))

    def test_non_draft_and_atomic_outbox_failure(self):
        contract = self.make_contract()
        Contract.objects.filter(pk=contract.pk).update(status='active')
        self.rejects(lambda: update_contract_draft(self.owner, contract.pk, project_id=self.project.pk,
            party_id=self.party.pk, contract_code='Changed', expected_version=1))
        before = self.snapshot()
        with mock.patch('core.services.DomainEvent.objects.create', side_effect=RuntimeError('outbox unavailable')):
            with self.assertRaises(RuntimeError):
                self.make_party(display_name='回滚测试')
        self.assertEqual(before, self.snapshot())

    def test_roles_revocation_and_module_gate(self):
        contract = self.make_contract()
        for role in ('member', 'auditor', 'pm'):
            member = self.member(role)
            self.rejects(lambda: create_party(member, display_name='拒绝', roles=['customer'], active_from=self.today, request_token=uuid.uuid4()))
        auditor = self.member('auditor')
        self.assertEqual(get_contract(auditor, contract.pk).pk, contract.pk)
        self.assertEqual(get_contract(self.pm, contract.pk).pk, contract.pk)
        self.assertEqual(get_party(self.pm, self.party.pk).pk, self.party.pk)
        unrelated = self.member('pm')
        self.assertFalse(list_contracts(unrelated).exists())
        self.assertFalse(list_parties(unrelated).exists())
        self.rejects(lambda: get_party(unrelated, self.party.pk))
        self.rejects(lambda: get_contract(unrelated, contract.pk))
        Membership.objects.filter(pk=self.owner.pk).update(active=False)
        self.rejects(lambda: self.make_party(display_name='撤权'))
        Membership.objects.filter(pk=self.owner.pk).update(active=True)
        PluginState.objects.create(organization=self.org, plugin_id='contracts', enabled=False, version='0.1.0', changed_by=self.owner.user)
        self.rejects(lambda: self.make_contract(contract_code='C2'))
        self.rejects(lambda: list_parties(self.owner))
        self.rejects(lambda: get_contract(self.owner, contract.pk))

    def test_fresh_member_precedes_company_lock_and_user_revocation(self):
        Membership.objects.filter(pk=self.owner.pk).update(active=False)
        with mock.patch('pmo_plugins.contracts.party_services.lock_company') as lock:
            self.rejects(lambda: self.make_party(display_name='撤权'))
            lock.assert_not_called()
        Membership.objects.filter(pk=self.owner.pk).update(active=True)
        get_user_model().objects.filter(pk=self.owner.user_id).update(is_active=False)
        self.rejects(lambda: self.make_contract())
        self.rejects(lambda: list_contracts(self.owner))

    def test_production_routes_csrf_get_and_unknown_input(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner.user)
        for page in ('party-save', 'contact-save', 'contract-save'):
            self.assertEqual(client.get('/modules/contracts/' + page + '/').status_code, 405)
            before = self.snapshot()
            self.assertEqual(client.post('/modules/contracts/' + page + '/', {}).status_code, 403)
            self.assertEqual(before, self.snapshot())
        response = client.get('/modules/contracts/parties/?role=customer')
        self.assertEqual(response.status_code, 200)
        class Inputs(HTMLParser):
            def __init__(self):
                super().__init__()
                self.values = {}
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'input' and 'name' in attrs:
                    self.values[attrs['name']] = attrs.get('value', '')
        inputs = Inputs()
        inputs.feed(response.content.decode())
        self.assertEqual(inputs.values['active_from'], self.today.isoformat())
        csrf = client.cookies['csrftoken'].value
        response = client.post('/modules/contracts/party-save/', dict(csrfmiddlewaretoken=csrf,
            display_name='HTTP客户', roles=['customer'], active_from=inputs.values['active_from'], request_token=str(uuid.uuid4())))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.get(response['Location']).status_code, 200)
        before = self.snapshot()
        response = client.post('/modules/contracts/party-save/', dict(csrfmiddlewaretoken=csrf,
            display_name='越权字段', roles=['customer'], active_from=self.today.isoformat(), request_token=str(uuid.uuid4()), status='active'))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(client.get('/modules/contracts/contracts/').status_code, 200)
        response = client.post('/modules/contracts/contact-save/', dict(csrfmiddlewaretoken=csrf,
            party_id=str(self.party.pk), name='浏览器联系人', email='demo@example.com', request_token=str(uuid.uuid4())))
        self.assertEqual(response.status_code, 302)
        response = client.post('/modules/contracts/contract-save/', dict(csrfmiddlewaretoken=csrf,
            project=str(self.project.pk), party=str(self.party.pk), contract_code='WEB-C1', request_token=str(uuid.uuid4())))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.get(response['Location']).status_code, 200)
        contract = Contract.objects.get(contract_code='WEB-C1')
        response = client.post('/modules/contracts/contract-save/', dict(csrfmiddlewaretoken=csrf,
            contract_id=str(contract.pk), expected_version=1,
            project=str(self.project.pk), party=str(self.party.pk), contract_code='WEB-C2'))
        self.assertEqual(response.status_code, 302)
        contract.refresh_from_db()
        self.assertEqual((contract.version, contract.contract_code), (2, 'WEB-C2'))

    def test_http_project_scope_enumeration_and_stale_input_retained(self):
        contract = self.make_contract()
        self.client.force_login(self.pm.user)
        for page, pk in [('party', self.party.pk), ('contract', contract.pk)]:
            self.assertEqual(self.client.get(f'/modules/contracts/{page}/?id={pk}').status_code, 200)
        unrelated = self.member('pm')
        self.client.force_login(unrelated.user)
        for page, pk in [('party', self.party.pk), ('contract', contract.pk)]:
            response = self.client.get(f'/modules/contracts/{page}/?id={pk}')
            self.assertEqual(response.status_code, 403)
            self.assertNotIn(self.party.display_name.encode(), response.content)
        self.client.force_login(self.owner.user)
        other = Organization.objects.create(name='跨公司')
        foreign_party = Counterparty.objects.create(organization=other, display_name='保密客户', roles=['customer'], active_from=self.today)
        foreign_contract = Contract.objects.create(organization=other, project=self.project, party=foreign_party, contract_code='SECRET')
        for page, pk in [('party', foreign_party.pk), ('contract', foreign_contract.pk)]:
            self.assertEqual(self.client.get(f'/modules/contracts/{page}/?id={pk}').status_code, 403)
        before = self.snapshot()
        response = self.client.post('/modules/contracts/contract-save/', dict(contract_id=str(contract.pk),
            expected_version=99, project=self.project.pk, party=str(self.party.pk), contract_code='保留输入'))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, '保留输入', status_code=400)
        self.assertEqual(before, self.snapshot())


@skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL real independent connections; SQLite does not prove locking')
class ContractsConcurrencyTests(Fixture, TransactionTestCase):
    def setUp(self):
        super().setUp()
        # Isolate the original contract lifecycle scenarios from newer dependants.
        from core.plugin_runtime import get_registry, set_enabled
        for plugin_id in ('management_revenue', 'delivery'):
            if plugin_id in get_registry().manifests:
                set_enabled(self.owner, plugin_id, False)

    def compete(self, kind):
        barrier = Barrier(2)
        def worker():
            close_old_connections()
            try:
                member = Membership.objects.get(pk=self.owner.pk)
                barrier.wait(timeout=10)
                try:
                    if kind == 'party':
                        create_party(member, display_name='并发客户', roles=['customer'], active_from=self.today, request_token=uuid.uuid4())
                    else:
                        create_contract_draft(member, project_id=self.project.pk, party_id=self.party.pk, contract_code='RACE', request_token=uuid.uuid4())
                    return 'created'
                except ValidationError:
                    return 'duplicate'
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: worker(), range(2)))
        self.assertCountEqual(results, ['created', 'duplicate'])

    def test_same_party_name_two_connections(self):
        self.compete('party')
        self.assertEqual(Counterparty.objects.filter(normalized_name='并发客户').count(), 1)

    def test_same_contract_code_two_connections(self):
        self.compete('contract')
        self.assertEqual(Contract.objects.filter(contract_code='RACE').count(), 1)

    def waiting_writer_refused(self, revoke=False):
        from core.plugin_runtime import set_enabled
        ready = Event()
        pids, results = {}, []
        def queued():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pids['queued'] = cursor.fetchone()[0]
                ready.set()
                try:
                    create_contract_draft(self.owner, project_id=self.project.pk, party_id=self.party.pk,
                        contract_code='WAITING', request_token=uuid.uuid4())
                    results.append('unexpected write')
                except PermissionDenied:
                    results.append('refused')
            except BaseException as error:
                results.append(repr(error))
            finally:
                connection.close()
        thread = Thread(target=queued)
        try:
            with transaction.atomic():
                Organization.objects.select_for_update().get(pk=self.org.pk)
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    holder = cursor.fetchone()[0]
                thread.start()
                self.assertTrue(ready.wait(5))
                deadline, blockers = time.monotonic() + 5, []
                while time.monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_blocking_pids(%s)", [pids['queued']])
                        blockers = cursor.fetchone()[0]
                    if holder in blockers:
                        break
                    time.sleep(0.01)
                self.assertIn(holder, blockers)
                if revoke:
                    Membership.objects.filter(pk=self.owner.pk).update(active=False)
                else:
                    set_enabled(self.owner, 'contracts', False)
                after_control_change = self.snapshot()
        finally:
            if thread.ident:
                thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, ['refused'])
        self.assertEqual(self.snapshot(), after_control_change)
        self.assertFalse(Contract.objects.filter(contract_code='WAITING').exists())

    def test_queued_direct_service_rechecks_committed_module_disable(self):
        self.waiting_writer_refused()

    def test_queued_direct_service_rechecks_committed_membership_revocation(self):
        self.waiting_writer_refused(revoke=True)

    def test_committed_write_precedes_waiting_disable_then_later_writes_refused(self):
        from core.plugin_runtime import set_enabled
        ready = Event()
        pids, errors = {}, []
        def disable():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    pids['disabler'] = cursor.fetchone()[0]
                ready.set()
                set_enabled(self.owner, 'contracts', False)
            except BaseException as error:
                errors.append(repr(error))
            finally:
                connection.close()
        thread = Thread(target=disable)
        try:
            with transaction.atomic():
                contract = self.make_contract(contract_code='BEFORE-DISABLE')
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    holder = cursor.fetchone()[0]
                thread.start()
                self.assertTrue(ready.wait(5))
                deadline, blockers = time.monotonic() + 5, []
                while time.monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT pg_blocking_pids(%s)', [pids['disabler']])
                        blockers = cursor.fetchone()[0]
                    if holder in blockers:
                        break
                    time.sleep(0.01)
                self.assertIn(holder, blockers)
        finally:
            if thread.ident:
                thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(Contract.objects.filter(pk=contract.pk, contract_code='BEFORE-DISABLE').exists())
        self.assertTrue(AuditEvent.objects.filter(entity_id=str(contract.pk), action='contracts.contract_draft.created').exists())
        before = self.snapshot()
        with self.assertRaises(PermissionDenied):
            self.make_contract(contract_code='AFTER-DISABLE')
        self.assertEqual(before, self.snapshot())
