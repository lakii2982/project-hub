"""PH-239 cash classification facts: one effective classification, append-only reversal."""
import uuid
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from core.models import (AccountingEvent, AccountingPeriod, AuditEvent, DomainEvent, LedgerEntry,
                         LedgerPosting, Organization, PluginState, SourceConsumption)
from pmo_plugins.cash import classification_services as cls
from pmo_plugins.cash import funding_services as funding
from pmo_plugins.cash import transaction_services as cash
from pmo_plugins.cash.models import (CashClassification, CashClassificationReversal, CashPartyMatch,
                                     CashReceipt, CashTransaction, FundingAccount)
from pmo_plugins.settlement.models import OpenItem, Settlement
from tests.test_v1_cash import CashFixture

RECORDED_TOPIC = 'cash.classification_recorded.v1'
REVERSED_TOPIC = 'cash.classification_reversed.v1'


class ClassificationFixture(CashFixture):
    def posted(self, *, member=None, **changes):
        values = {'gross_cents': 53000}
        values.update(changes)
        row = self.tx(member=member, **values)
        return cash.record_cash_transaction(
            member or self.owner, row.pk, expected_version=row.version,
            request_token=uuid.uuid4(), dry_run=False)

    def classify(self, row=None, *, classification='receipt', member=None, **changes):
        values = {'cash_transaction_id': (row or self.posted()).pk,
                  'classification': classification,
                  'evidence': [{'reference': '分类依据 B-001'}],
                  'request_token': uuid.uuid4(), 'dry_run': False}
        values.update(changes)
        return cls.record_classification(member or self.owner, **values)

    def reverse(self, row, *, member=None, **changes):
        values = {'classification_id': row.pk, 'reason': '分类归属录错，撤分类重做',
                  'evidence': [{'reference': '撤分类依据 R-001'}],
                  'request_token': uuid.uuid4(), 'expected_version': row.version,
                  'dry_run': False}
        values.update(changes)
        return cls.reverse_classification(member or self.owner, **values)

    def facts(self):
        return tuple(model.objects.count() for model in (
            FundingAccount, CashTransaction, CashPartyMatch, CashReceipt, CashClassification,
            CashClassificationReversal, DomainEvent, AuditEvent, AccountingEvent, LedgerPosting,
            LedgerEntry, Settlement, OpenItem, SourceConsumption))


class ClassificationLifecycleTests(ClassificationFixture, TestCase):
    def test_receipt_classification_writes_canonical_event_and_receipt(self):
        row = self.posted()
        before = self.facts()
        classification = self.classify(row)
        after = self.facts()
        self.assertEqual((classification.classification, classification.status,
                          classification.version), ('receipt', 'active', 1))
        ref = classification.classification_ref
        self.assertEqual((ref['namespace'], ref['kind']), ('cash', 'cash_classification'))
        self.assertEqual(after[3], before[3] + 1)  # CashReceipt
        self.assertEqual(after[4], before[4] + 1)  # CashClassification
        self.assertEqual(after[6], before[6] + 2)  # generic outbox + canonical event
        self.assertEqual(after[7], before[7] + 2)  # generic audit + suppressed source
        # PH-239 bridge: one AccountingEvent, one cash_in posting and one bank_effect claim.
        self.assertEqual(after[8], before[8] + 1)
        self.assertEqual(after[9], before[9] + 1)
        self.assertEqual(after[13], before[13] + 1)
        self.assertEqual(before[10:13], after[10:13])
        bridge = AccountingEvent.objects.get(source=ref)
        self.assertEqual((bridge.event_type, bridge.claim_effect_cents,
                          bridge.occurred_on, bridge.posting_period),
                         ('cash.classification', 53000, row.transaction_date,
                          row.transaction_date.strftime('%Y-%m')))
        posting = bridge.postings.get()
        self.assertEqual((posting.book, posting.amount_cents, posting.scope_kind,
                          posting.scope_id, posting.reverses_posting_id),
                         ('cash_in', 53000, 'funding_account',
                          str(row.funding_account_id), None))
        claim = SourceConsumption.objects.get(consumer='cash')
        self.assertEqual((claim.coverage_key, claim.source_id, claim.amount_cents),
                         ('bank_effect', row.pk, 53000))
        event_ref = {'namespace': 'core', 'kind': 'accounting_event',
                     'id': str(bridge.pk), 'version': bridge.version}
        canonical = DomainEvent.objects.get(topic=RECORDED_TOPIC)
        self.assertEqual(canonical.data['source'], ref)
        self.assertEqual(canonical.data['topic'], RECORDED_TOPIC)
        self.assertEqual(canonical.data['schema_version'], 1)
        self.assertEqual(canonical.data['organization_id'], str(self.org.pk))
        self.assertEqual(canonical.data['payload'],
                         {'changed_refs': [ref], 'posting_event_refs': [event_ref]})
        receipt = CashReceipt.objects.get(action='cash_classification.record')
        self.assertEqual(receipt.result, ref)
        self.assertEqual(AuditEvent.objects.filter(action='cash.classification.recorded').count(), 1)
        snapshot = cls.classification_snapshot(self.owner, classification.pk)
        self.assertEqual((snapshot['classification'], snapshot['status'],
                          snapshot['gross_cents'], snapshot['book']),
                         ('receipt', 'active', 53000, 'cash_in'))
        self.assertEqual(len(cls.classification_list(self.owner, cash_transaction_id=row.pk)), 1)
        coverage = cls.cash_book_coverage(self.owner, self.account.pk)
        self.assertEqual((coverage['classified_count'],
                          coverage['unclassified_posted_count']), (1, 0))
        self.assertEqual((coverage['from_ledger_cash_in_cents'],
                          coverage['from_ledger_cash_out_cents'],
                          coverage['book_net_cents']), (53000, 0, 53000))
        self.assertIsNotNone(coverage['last_event_at'])
        self.assertIn('非银行余额', coverage['estimate_label'])

    def test_reverse_is_append_only_and_allows_reclassification(self):
        row = self.posted(payee={'kind': 'counterparty', 'id': str(self.party.pk)})
        classification = self.classify(row)
        before = self.facts()
        reversal = self.reverse(classification)
        after = self.facts()
        classification.refresh_from_db()
        self.assertEqual((classification.status, classification.version), ('reversed', 1))
        self.assertEqual((reversal.status, reversal.reason), ('posted',
                         '分类归属录错，撤分类重做'))
        self.assertEqual(after[3], before[3] + 1)  # reversal receipt
        self.assertEqual(after[5], before[5] + 1)  # append-only reversal row
        self.assertEqual(after[6], before[6] + 2)
        self.assertEqual(after[7], before[7] + 2)
        self.assertEqual(after[8], before[8] + 1)   # mirrored accounting event
        self.assertEqual(after[9], before[9] + 1)   # mirrored negative posting
        self.assertEqual(after[13], before[13])     # claim is re-used, never duplicated
        original_event = AccountingEvent.objects.get(
            source=classification.classification_ref)
        reversal_event = AccountingEvent.objects.get(source=reversal.reversal_ref)
        self.assertEqual((reversal_event.event_type, reversal_event.claim_effect_cents,
                          reversal_event.reverses_event_id, reversal_event.claim_id),
                         ('cash.classification.reversed', -53000, original_event.pk,
                          original_event.claim_id))
        reversal_posting = reversal_event.postings.get()
        self.assertEqual((reversal_posting.book, reversal_posting.amount_cents,
                          reversal_posting.reverses_posting_id,
                          reversal_posting.scope_id),
                         ('cash_in', -53000, original_event.postings.get().pk,
                          str(row.funding_account_id)))
        self.assertEqual(original_event.postings.get().amount_cents, 53000)
        reversal_ref = {'namespace': 'core', 'kind': 'accounting_event',
                        'id': str(reversal_event.pk), 'version': reversal_event.version}
        canonical = DomainEvent.objects.get(topic=REVERSED_TOPIC)
        self.assertEqual(canonical.data['source'], reversal.reversal_ref)
        self.assertEqual(canonical.data['payload']['original_classification_ref'],
                         classification.classification_ref)
        self.assertEqual(canonical.data['payload']['changed_refs'], [reversal.reversal_ref])
        self.assertEqual(canonical.data['payload']['posting_event_refs'], [reversal_ref])
        self.assertEqual(canonical.data['payload']['reversed_payroll_link_refs'], [])
        self.assertEqual(cls.classification_snapshot(
            self.owner, classification.pk)['status'], 'reversed')
        replacement = self.classify(row, classification='advance')
        self.assertEqual(replacement.status, 'active')
        self.assertEqual(SourceConsumption.objects.filter(consumer='cash').count(), 1)
        rows = cls.classification_list(self.owner, cash_transaction_id=row.pk)
        self.assertEqual(sorted((item['classification'], item['status']) for item in rows),
                         [('advance', 'active'), ('receipt', 'reversed')])
        self.assertEqual(DomainEvent.objects.filter(topic=RECORDED_TOPIC).count(), 2)

    def test_idempotent_replay_and_changed_input_conflict(self):
        row = self.posted()
        token = uuid.uuid4()
        first = self.classify(row, request_token=token)
        before = self.facts()
        replay = self.classify(row, request_token=token)
        self.assertEqual((replay.pk, replay.classification), (first.pk, 'receipt'))
        self.assertEqual(before, self.facts())
        self.denied(lambda: self.classify(row, classification='tax', request_token=token))
        reverse_token = uuid.uuid4()
        reversal = self.reverse(first, request_token=reverse_token)
        before = self.facts()
        again = self.reverse(first, request_token=reverse_token)
        self.assertEqual(again.pk, reversal.pk)
        self.assertEqual(before, self.facts())
        self.denied(lambda: self.reverse(first, reason='另一个原因',
                                         request_token=reverse_token))

    def test_dry_run_has_no_side_effects(self):
        row = self.posted()
        before = self.facts()
        preview = self.classify(row, dry_run=True)
        self.assertEqual((preview.classification, preview.status), ('receipt', 'active'))
        self.assertEqual(before, self.facts())
        classification = self.classify(row)
        before = self.facts()
        reversal_preview = self.reverse(classification, dry_run=True)
        self.assertEqual(reversal_preview.status, 'posted')
        self.assertEqual(before, self.facts())

    def test_rejections_have_no_half_write(self):
        draft = self.tx()
        self.denied(lambda: self.classify(draft))
        out_row = self.posted(direction='out', gross_cents=1200)
        self.denied(lambda: self.classify(out_row, classification='receipt'))
        in_row = self.posted()
        self.denied(lambda: self.classify(in_row, classification='payment'))
        self.denied(lambda: self.classify(in_row, classification='advance'))
        self.denied(lambda: self.classify(in_row, classification='payroll'))
        self.denied(lambda: self.classify(in_row, classification='fee'))
        self.denied(lambda: self.classify(in_row, classification=''))
        self.denied(lambda: self.classify(in_row, evidence=[]))
        classification = self.classify(in_row)
        self.denied(lambda: self.classify(in_row, classification='tax'))
        self.denied(lambda: self.reverse(classification, expected_version=2))
        for value in (True, 1.0, 0, -1, '1'):
            self.denied(lambda value=value: self.reverse(classification,
                                                         expected_version=value))
        for value in (0, 1, 1.0, None, 'false'):
            self.denied(lambda value=value: self.classify(in_row, dry_run=value))
        self.denied(lambda: self.reverse(classification, reason=''))
        self.denied(lambda: self.reverse(classification, reason='x' * 1001))
        self.denied(lambda: self.reverse(classification, evidence=[]))

    def test_period_lock_close_and_future_rules(self):
        row = self.posted()
        self.org.locked_through = self.today
        self.org.save()
        self.denied(lambda: self.classify(row))
        self.org.locked_through = None
        self.org.save()
        AccountingPeriod.objects.update_or_create(
            organization=self.org, period=self.today.strftime('%Y-%m'),
            defaults={'status': 'posted', 'closed_through': self.today})
        self.denied(lambda: self.classify(row))
        AccountingPeriod.objects.filter(organization=self.org).delete()
        future = CashTransaction.objects.create(
            organization=self.org, funding_account=self.account,
            transaction_date=self.today + timedelta(days=1), direction='in',
            gross_cents=1000, external_source_key='FUTURE-CLASSIFY',
            payee={'kind': 'unidentified', 'bank_label': None},
            evidence=[{'reference': '未来流水'}], status='posted', version=2,
            created_by=self.owner)
        self.denied(lambda: self.classify(future))
        classification = self.classify(row)
        self.org.locked_through = self.today
        self.org.save()
        self.denied(lambda: self.reverse(classification))
        self.org.locked_through = None
        self.org.save()
        self.denied(lambda: self.classify(row, occurred_on=self.today - timedelta(days=1)))
        self.denied(lambda: self.classify(row, posting_period='2026-1'))

    def test_advance_requires_effective_counterparty(self):
        draft = self.tx(payee={'kind': 'unidentified', 'bank_label': '客户回款'})
        match = self.identify(draft, {'kind': 'counterparty', 'id': str(self.party.pk)})
        row = cash.record_cash_transaction(
            self.owner, draft.pk, expected_version=draft.version,
            request_token=uuid.uuid4(), dry_run=False)
        self.assertIsNotNone(match.pk)
        classification = self.classify(row, classification='advance')
        self.assertEqual(classification.classification, 'advance')
        other = self.posted(payee={'kind': 'unidentified', 'bank_label': '未识别'})
        self.denied(lambda: self.classify(other, classification='advance'))

    def test_roles_and_read_scope(self):
        row = self.posted()
        classification = self.classify(row)
        for actor in (self.auditor, self.pm, self.plain):
            role_row = self.posted()
            self.denied(lambda actor=actor, row=role_row: self.classify(row, member=actor))
            self.denied(lambda actor=actor: self.reverse(classification, member=actor))
        for actor in (self.owner, self.finance, self.auditor):
            self.assertEqual(cls.classification_snapshot(
                actor, classification.pk)['classification'], 'receipt')
            self.assertTrue(cls.classification_list(actor))
            self.assertTrue(cls.cash_book_coverage(actor, self.account.pk))
        for actor in (self.pm, self.plain):
            self.denied(lambda actor=actor: cls.classification_snapshot(
                actor, classification.pk))
            self.denied(lambda actor=actor: cls.classification_list(actor))
            self.denied(lambda actor=actor: cls.cash_book_coverage(actor, self.account.pk))
        finance_row = self.posted()
        self.assertEqual(self.classify(finance_row, member=self.finance).status, 'active')

    def test_cross_org_and_module_disable(self):
        other = Organization.objects.create(name='其他公司')
        other_user = self.member('owner')
        other_member = type(other_user).objects.create(
            organization=other, user=other_user.user, role='owner')
        other_owner = type(other_user).objects.get(pk=other_member.pk)
        account = funding.save_funding_account(
            other_owner, name='其他公司户', request_token=uuid.uuid4(), dry_run=False)
        account = funding.activate_funding_account(
            other_owner, account.pk, expected_version=account.version,
            request_token=uuid.uuid4(), dry_run=False)
        foreign = cash.save_cash_transaction(
            other_owner, funding_account_id=account.pk, transaction_date=self.today,
            direction='in', gross_cents=1000, external_source_key='OTHER-1',
            payee={'kind': 'unidentified'}, evidence=[{'reference': '外部流水'}],
            request_token=uuid.uuid4(), dry_run=False)
        foreign = cash.record_cash_transaction(
            other_owner, foreign.pk, expected_version=foreign.version,
            request_token=uuid.uuid4(), dry_run=False)
        self.denied(lambda: self.classify(foreign))
        PluginState.objects.update_or_create(organization=self.org, plugin_id='cash',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        self.denied(lambda: self.classify(self.posted()))
        self.assertEqual(cls.classification_list(self.auditor), [])

    def test_append_only_database_guards(self):
        row = self.posted()
        classification = self.classify(row)
        reversal = self.reverse(classification)
        with self.assertRaises((IntegrityError, ValidationError)):
            with transaction.atomic():
                CashClassification.objects.filter(pk=classification.pk).delete()
        with self.assertRaises((IntegrityError, ValidationError)):
            with transaction.atomic():
                CashClassificationReversal.objects.filter(pk=reversal.pk).delete()
        with self.assertRaises((IntegrityError, ValidationError)):
            with transaction.atomic():
                CashClassification.objects.filter(pk=classification.pk).update(
                    classification='payment')
        with self.assertRaises((IntegrityError, ValidationError)):
            with transaction.atomic():
                CashClassification.objects.filter(pk=classification.pk).update(
                    status='reversed')
        with self.assertRaises(ValidationError):
            classification.classification = 'payment'
            classification.save()
        with self.assertRaises(ValidationError):
            reversal.reason = '再次修改'
            reversal.save()


class ClassificationVerifyIntegrityTests(ClassificationFixture, TestCase):
    def verify(self):
        from io import StringIO
        import json
        from django.core.management import call_command
        out = StringIO()
        call_command('verify_integrity', '--json', stdout=out)
        return json.loads(out.getvalue())

    def assert_verify_error(self, *needles):
        from io import StringIO
        import json
        from django.core.management import call_command
        from django.core.management.base import CommandError
        out = StringIO()
        with self.assertRaises(CommandError):
            call_command('verify_integrity', '--json', stdout=out)
        payload = json.loads(out.getvalue())
        self.assertFalse(payload['ok'])
        for needle in needles:
            self.assertTrue(any(needle in error for error in payload['errors']),
                            payload['errors'])

    def test_valid_classification_and_reversal_pass_integrity(self):
        row = self.posted(payee={'kind': 'counterparty', 'id': str(self.party.pk)})
        classification = self.classify(row)
        self.assertTrue(self.verify()['ok'])
        self.reverse(classification)
        self.assertTrue(self.verify()['ok'])
        self.classify(row, classification='advance')
        self.assertTrue(self.verify()['ok'])

    def test_missing_or_tampered_recorded_event_fails_integrity(self):
        self.classify(self.posted())
        event = DomainEvent.objects.get(topic=RECORDED_TOPIC)
        original = dict(event.data)
        event.data = {**original, 'payload': {'changed_refs': [], 'posting_event_refs': []}}
        event.save(update_fields=['data'])
        self.assert_verify_error('recorded_event_envelope')
        DomainEvent.objects.filter(pk=event.pk).update(data=original)
        self.assertTrue(self.verify()['ok'])
        DomainEvent.objects.filter(pk=event.pk).delete()
        self.assert_verify_error('recorded_event_count')

    def test_reversed_event_must_point_to_original_classification(self):
        classification = self.classify(self.posted())
        self.reverse(classification)
        event = DomainEvent.objects.get(topic=REVERSED_TOPIC)
        payload = dict(event.data['payload'])
        payload['original_classification_ref'] = {
            'namespace': 'cash', 'kind': 'cash_classification',
            'id': str(uuid.uuid4()), 'version': 1}
        event.data = {**event.data, 'payload': payload}
        event.save(update_fields=['data'])
        self.assert_verify_error('reversed_event_envelope')
        event.refresh_from_db()
        payload['original_classification_ref'] = classification.classification_ref
        event.data = {**event.data, 'payload': payload}
        event.save(update_fields=['data'])
        self.assertTrue(self.verify()['ok'])
        DomainEvent.objects.filter(pk=event.pk).delete()
        self.assert_verify_error('reversed_event_count')

    def test_active_classification_with_reversal_row_fails_integrity(self):
        classification = self.classify(self.posted())
        CashClassificationReversal.objects.create(
            organization=self.org, classification=classification,
            cash_transaction=classification.cash_transaction, status='posted',
            reason='伪造撤分类', evidence=[{'reference': '伪造依据'}],
            occurred_on=self.today, posting_period=self.today.strftime('%Y-%m'),
            created_by=self.owner)
        self.assert_verify_error('active_has_reversal')

    def test_cross_org_classification_insert_is_rejected_by_guard(self):
        row = self.posted()
        other = Organization.objects.create(name='校验其他公司')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CashClassification.objects.create(
                    organization=other, cash_transaction=row, classification='receipt',
                    status='active', version=1, evidence=[{'reference': '跨组织分类'}],
                    created_by=self.owner)
