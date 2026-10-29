"""Transactional delivery owner. All public mutations default to simulation."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import uuid
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from core.access import require_project
from core.models import DomainEvent, Milestone
from core.plugin_runtime import is_enabled
from core.services import audit, lock_company
from pmo_plugins.contracts.party_services import _fresh_member, _digest, _request_token, _version
from pmo_plugins.contracts.models import Contact
from pmo_plugins.contracts.approval_services import approved_line_snapshot as _contract_snapshot, approved_line_choices
from .models import Acceptance, AcceptanceCoverage, AcceptanceReversal, CustomerConfirmationEvidence, DeliveryReceipt


def approved_line_snapshot(member, ref, day):
    from core.models import Project
    from pmo_plugins.contracts.models import ContractLine, ContractVersion, MethodPolicySelection
    value=_contract_snapshot(member,ref,day)
    return {**value,'project':Project.objects.get(pk=value['project_id']),
            'line':ContractLine.objects.get(pk=value['line_id']),
            'version':ContractVersion.objects.get(pk=value['contract_version_id']),
            'selection':MethodPolicySelection.objects.get(pk=value['selection_id'])}


def _dry(value):
    if type(value) is not bool:
        raise ValidationError('dry_run必须为布尔值')
    return value


def _fresh(member, write=False):
    member = _fresh_member(member)
    if member.role not in ({'owner','finance','pm'} if write else {'owner','finance','pm','auditor'}):
        raise PermissionDenied('无权办理客户验收')
    if not is_enabled(member.organization,'delivery') or not is_enabled(member.organization,'contracts'):
        raise PermissionDenied('客户验收或合同模块已停用')
    return member


def _locked(member):
    member = _fresh(member,True)
    lock_company(member)
    return _fresh(member,True)


def _project(member, project):
    require_project(member, project, finance=True)


def _date(value):
    if type(value) is not date or value > timezone.localdate():
        raise ValidationError('客户确认日期无效或在未来')
    return value


def _period(member, day):
    from core.revenue_accounting import check_revenue_period
    check_revenue_period(member.organization,day,allow_create=False)


def _text(value, maximum):
    if not isinstance(value,str) or not value.strip() or len(value.strip())>maximum:
        raise ValidationError('请填写完整依据或原因并遵守长度限制')
    return value.strip()


def _receipt(member, action, token, payload, model):
    token = _request_token(token)
    digest = _digest({'action':action,'actor':member.pk,'payload':payload})
    receipt = DeliveryReceipt.objects.filter(organization=member.organization,request_token=token).first()
    if receipt:
        if receipt.action != action or receipt.digest != digest:
            raise ValidationError('请求标识已用于其他输入')
        result = model.objects.get(pk=receipt.result_id)
        _project(member,result.project)
        return token,digest,result
    return token,digest,None


def _record(member, action, token, digest, row):
    DeliveryReceipt.objects.create(organization=member.organization,action=action,request_token=token,digest=digest,result_id=row.pk)


def _get(member, acceptance_id):
    try:
        row = Acceptance.objects.select_related('project','organization','contract_line','contract_version','selection').get(pk=acceptance_id,organization=member.organization)
    except (Acceptance.DoesNotExist,ValueError,ValidationError):
        raise PermissionDenied('验收不存在或无权访问')
    _project(member,row.project)
    return row


def evidence_ref(row):
    return {'evidence_id':str(row.pk),'sha256':row.sha256,'kind':'customer_confirmation'}


@transaction.atomic
def register_customer_evidence(member, *, contract_line_ref, contact_id, confirmed_on, channel, reference, statement, request_token, dry_run=True):
    _dry(dry_run)
    member = _locked(member)
    day = _date(confirmed_on)
    snapshot = approved_line_snapshot(member,contract_line_ref,day)
    project,line = snapshot['project'],snapshot['line']
    _project(member,project)
    _period(member,day)
    try:
        contact = Contact.objects.get(pk=contact_id,organization=member.organization,status='active',party_id=line.counterparty_id)
    except (Contact.DoesNotExist,ValueError,ValidationError):
        raise ValidationError('联系人须为本合同客户的有效联系人')
    payload = {'contract_line_ref':contract_line_ref,'contact_id':str(contact.pk),'confirmed_on':day.isoformat(),'channel':_text(channel,100),'reference':_text(reference,200),'statement':_text(statement,2000)}
    token,digest,existing = _receipt(member,'evidence',request_token,payload,CustomerConfirmationEvidence)
    if existing: return existing
    row = CustomerConfirmationEvidence(organization=member.organization,project=project,contract_line=line,contact=contact,contact_snapshot={'name':contact.name,'email':contact.email,'phone':contact.phone,'party_id':str(contact.party_id)},confirmed_on=day,channel=payload['channel'],reference=payload['reference'],statement=payload['statement'],sha256=_digest(payload),created_by_member=member)
    if not dry_run:
        row.save()
        _record(member,'evidence',token,digest,row)
        audit(member,'delivery.evidence.registered',row,payload)
    return row


def _coverages(member, snapshot, coverages, exclude=None, check_active=True):
    if not isinstance(coverages,list) or not 1<=len(coverages)<=1000:
        raise ValidationError('节点覆盖须为1至1000项')
    config = snapshot['pricing_configuration']
    if config.get('template')!='fixed_milestone': raise ValidationError('本片只支持固定节点')
    allocations = {str(x['milestone_id']):x['allocated_net_cents'] for x in config['milestones']}
    if sum(allocations.values()) != snapshot['net_cents']: raise ValidationError('合同节点分配金额不一致')
    result=[]
    for item in coverages:
        if not isinstance(item,dict) or set(item)!={'milestone_id','from_basis_points','to_basis_points'}: raise ValidationError('节点覆盖字段无效')
        key=str(item['milestone_id']); start=item['from_basis_points']; end=item['to_basis_points']
        if key not in allocations or type(start) is not int or type(end) is not int or not 0<=start<end<=10000: raise ValidationError('节点或覆盖范围无效')
        milestone=Milestone.objects.filter(pk=key,project=snapshot['project']).first()
        if not milestone: raise ValidationError('节点不属于合同项目')
        if any(str(x['milestone'].pk)==key and start<x['to_basis_points'] and end>x['from_basis_points'] for x in result): raise ValidationError('草稿节点覆盖重叠')
        rows=AcceptanceCoverage.objects.filter(acceptance__organization=member.organization,acceptance__project=snapshot['project'],milestone=milestone,acceptance__status='confirmed',from_basis_points__lt=end,to_basis_points__gt=start)
        if exclude: rows=rows.exclude(acceptance=exclude)
        if check_active and rows.exists(): raise ValidationError('节点覆盖与已有验收重叠')
        amount=lambda bp:int((Decimal(allocations[key])*bp/10000).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
        result.append({'milestone':milestone,'from_basis_points':start,'to_basis_points':end,'accepted_net_cents':amount(end)-amount(start)})
    return result


def _evidence(member, refs, line, day):
    if not isinstance(refs,list) or not refs: raise ValidationError('请登记真实客户确认依据')
    for ref in refs:
        if not isinstance(ref,dict) or set(ref)!={'evidence_id','sha256','kind'} or ref['kind']!='customer_confirmation': raise ValidationError('客户依据引用无效')
        row=CustomerConfirmationEvidence.objects.filter(pk=ref['evidence_id'],organization=member.organization,contract_line=line,confirmed_on=day).first()
        if not row or row.sha256!=ref['sha256']: raise ValidationError('客户依据不存在、日期不符或摘要不符')


def _income(member,row):
    from pmo_plugins.management_revenue.services import acceptance_income_state
    return acceptance_income_state(member,row.pk)


def _replacement(member, ref, line, coverages):
    old=None
    if ref is not None:
        if not isinstance(ref,dict) or set(ref)!={'namespace','kind','id','version'} or ref['namespace']!='delivery' or ref['kind']!='acceptance' or type(ref['version']) is not int: raise ValidationError('替代来源无效')
        old=_get(member,ref['id'])
        if old.status!='reversed' or old.confirmed_version!=ref['version'] or old.contract_line_id!=line.pk: raise ValidationError('替代来源须为同合同明细已反验收')
        income=_income(member,old)
        if income['has_recognition'] and not income['is_reversed']: raise ValidationError('替代来源仍有未冲收入')
    # Any reuse of historical reversed coverage requires its explicit chain.
    for item in coverages:
        history=AcceptanceCoverage.objects.filter(acceptance__organization=member.organization,acceptance__project=line.contract.project,milestone=item['milestone'],acceptance__status='reversed',from_basis_points__lt=item['to_basis_points'],to_basis_points__gt=item['from_basis_points'])
        if history.exists() and (old is None or not history.filter(acceptance=old).exists()): raise ValidationError('重用已反节点必须明确替代来源')
    return old


@transaction.atomic
def save_acceptance(member, *, contract_line_ref, confirmed_on, evidence, milestone_coverages, request_token, acceptance_id=None, expected_version=None, replaces_acceptance_ref=None, dry_run=True):
    _dry(dry_run)
    member=_locked(member); day=_date(confirmed_on)
    snapshot=approved_line_snapshot(member,contract_line_ref,day)
    _project(member,snapshot['project']); _period(member,day)
    payload={'contract_line_ref':contract_line_ref,'confirmed_on':day,'evidence':evidence,'milestone_coverages':milestone_coverages,'acceptance_id':acceptance_id,'expected_version':expected_version,'replaces_acceptance_ref':replaces_acceptance_ref}
    token,digest,existing=_receipt(member,'save',request_token,payload,Acceptance)
    if existing:return existing
    row=_get(member,acceptance_id) if acceptance_id else Acceptance(organization=member.organization,created_by_member=member)
    if acceptance_id and (row.status!='draft' or row.version!=_version(expected_version)): raise ValidationError('仅可编辑当前版本草稿')
    if acceptance_id and member.role=='pm' and row.created_by_member_id!=member.pk: raise PermissionDenied('仅可编辑本人草稿')
    coverage=_coverages(member,snapshot,milestone_coverages,row if acceptance_id else None)
    _evidence(member,evidence,snapshot['line'],day)
    old=_replacement(member,replaces_acceptance_ref,snapshot['line'],coverage)
    for field,value in {'project':snapshot['project'],'contract_line':snapshot['line'],'contract_version':snapshot['version'],'selection':snapshot['selection'],'line_ref':snapshot['line_ref'],'policy_version':snapshot['policy_version'],'pricing_configuration':snapshot['pricing_configuration'],'approved_digest':snapshot['digest'],'evidence':evidence,'confirmed_on':day,'accepted_net_cents':sum(x['accepted_net_cents'] for x in coverage),'replaces_acceptance':old}.items():setattr(row,field,value)
    if acceptance_id:row.version+=1
    if not dry_run:
        row.save(); row.coverages.all().delete()
        AcceptanceCoverage.objects.bulk_create([AcceptanceCoverage(acceptance=row,**x) for x in coverage])
        _record(member,'save',token,digest,row); audit(member,'delivery.acceptance.saved',row,payload)
    return row


@transaction.atomic
def save_acceptance_with_evidence(member, *, contract_line_ref, contact_id, confirmed_on, channel, reference, statement, milestone_coverages, request_token, acceptance_id=None, expected_version=None, replaces_acceptance_ref=None, dry_run=True):
    _dry(dry_run)
    # Stable derived evidence nonce makes unknown-outcome retries deterministic.
    token=_request_token(request_token)
    evidence=register_customer_evidence(member,contract_line_ref=contract_line_ref,contact_id=contact_id,confirmed_on=confirmed_on,channel=channel,reference=reference,statement=statement,request_token=uuid.uuid5(token,'customer-evidence'),dry_run=False)
    row=save_acceptance(member,contract_line_ref=contract_line_ref,confirmed_on=confirmed_on,evidence=[evidence_ref(evidence)],milestone_coverages=milestone_coverages,request_token=token,acceptance_id=acceptance_id,expected_version=expected_version,replaces_acceptance_ref=replaces_acceptance_ref,dry_run=dry_run)
    if dry_run: transaction.set_rollback(True)
    return row


def _action(member, acceptance_id, *, expected_version, request_token, action, note='', decision=None, dry_run=True):
    _dry(dry_run)
    member=_locked(member); row=_get(member,acceptance_id)
    payload={'id':str(row.pk),'expected_version':expected_version,'note':note,'decision':decision}
    token,digest,existing=_receipt(member,action,request_token,payload,Acceptance)
    if existing:return existing
    if row.version!=_version(expected_version):raise ValidationError('记录版本过期')
    _period(member,row.confirmed_on)
    if action in {'review','reverse'}:
        if member.role not in {'owner','finance'} or member.pk in {row.created_by_member_id,row.submitted_by_member_id}:raise PermissionDenied('需要另一位公司负责人或财务独立办理')
    if action=='submit':
        if row.status!='draft':raise ValidationError('仅可提交草稿')
        snapshot=approved_line_snapshot(member,row.line_ref,row.confirmed_on)
        _evidence(member,row.evidence,row.contract_line,row.confirmed_on)
        _coverages(member,snapshot,list(row.coverages.values('milestone_id','from_basis_points','to_basis_points')),row)
        row.status='submitted';row.submitted_by_member=member
    elif action=='review':
        if row.status!='submitted' or decision not in {'approve','return','reject'}:raise ValidationError('验收状态或决定无效')
        row.review_note=_text(note,2000)
        if decision=='approve':
            snapshot=approved_line_snapshot(member,row.line_ref,row.confirmed_on)
            if any(snapshot[key]!=getattr(row,field) for key,field in [('digest','approved_digest'),('line_ref','line_ref'),('policy_version','policy_version'),('pricing_configuration','pricing_configuration')]) or snapshot['selection'].pk!=row.selection_id or snapshot['version'].pk!=row.contract_version_id:raise ValidationError('合同快照发生变化')
            coverage=_coverages(member,snapshot,list(row.coverages.values('milestone_id','from_basis_points','to_basis_points')),row)
            _evidence(member,row.evidence,row.contract_line,row.confirmed_on)
            ref=source_ref(row.replaces_acceptance) if row.replaces_acceptance_id else None
            _replacement(member,ref,row.contract_line,coverage)
            saved=list(row.coverages.order_by('milestone_id','from_basis_points').values_list('milestone_id','from_basis_points','to_basis_points','accepted_net_cents'))
            expected=sorted([(x['milestone'].pk,x['from_basis_points'],x['to_basis_points'],x['accepted_net_cents']) for x in coverage])
            if saved!=expected or sum(x['accepted_net_cents'] for x in coverage)!=row.accepted_net_cents:raise ValidationError('验收金额快照不一致')
            row.status='confirmed';row.confirmed_by_member=member;row.confirmed_at=timezone.now()
        else:row.status='draft'
    elif action=='withdraw':
        if row.status not in {'draft','submitted'}:raise ValidationError('仅可撤回草稿或待审验收')
        if member.role not in {'owner','finance'} and row.created_by_member_id!=member.pk:raise PermissionDenied('仅可撤回本人验收')
        row.review_note=_text(note,2000);row.status='withdrawn'
    elif action=='reverse':
        if row.status!='confirmed':raise ValidationError('仅可反冲已确认验收')
        confirmed_acceptance_snapshot(member,row.pk,for_recognition=False)
        note=_text(note,2000);income=_income(member,row)
        if income['has_recognition'] and not income['is_reversed']:raise ValidationError('须先完整反冲管理收入')
        row.status='reversed'
    row.version+=1
    canonical=action=='review' and decision=='approve'
    if canonical:
        row.confirmed_version=row.version
        row.approved_input_snapshot=_approved_input(row)
    if not dry_run:
        if canonical:
            record=audit(member,'plugin.delivery.accepted',row,{'approved_input':row.approved_input_snapshot,'approved_digest':_digest(row.approved_input_snapshot)})
            row.confirmation_audit=record
        row.save()
        if action=='reverse':AcceptanceReversal.objects.create(acceptance=row,reason=note,actor=member)
        _record(member,action,token,digest,row)
        if not canonical: record=audit(member,'delivery.acceptance.'+action,row,payload)
        if canonical:
            event_id=uuid.uuid4(); ref=source_ref(row)
            DomainEvent.objects.create(event_id=event_id,organization=member.organization,source=record,topic='delivery.accepted.v1',data={'event_id':str(event_id),'topic':'delivery.accepted.v1','schema_version':1,'organization_id':str(member.organization_id),'source':ref,'occurred_at':timezone.now().isoformat(),'payload':{'changed_refs':[ref],'posting_event_refs':[]}})
    return row


@transaction.atomic
def submit_acceptance(member, acceptance_id, *, expected_version, request_token, dry_run=True):
    _dry(dry_run)
    return _action(member,acceptance_id,expected_version=expected_version,request_token=request_token,action='submit',dry_run=dry_run)


@transaction.atomic
def review_acceptance(member, acceptance_id, *, expected_version, request_token, decision, note, dry_run=True):
    _dry(dry_run)
    return _action(member,acceptance_id,expected_version=expected_version,request_token=request_token,action='review',decision=decision,note=note,dry_run=dry_run)


@transaction.atomic
def withdraw_acceptance(member, acceptance_id, *, expected_version, request_token, reason, dry_run=True):
    _dry(dry_run)
    return _action(member,acceptance_id,expected_version=expected_version,request_token=request_token,action='withdraw',note=reason,dry_run=dry_run)


@transaction.atomic
def reverse_acceptance(member, acceptance_id, *, expected_version, request_token, reason, dry_run=True):
    _dry(dry_run)
    return _action(member,acceptance_id,expected_version=expected_version,request_token=request_token,action='reverse',note=reason,dry_run=dry_run)


def source_ref(row):
    return {'namespace':'delivery','kind':'acceptance','id':str(row.pk),'version':row.confirmed_version or row.version}


def get_acceptance(member, acceptance_id):return _get(_fresh(member),acceptance_id)


def list_acceptances(member):
    member=_fresh(member)
    rows=Acceptance.objects.filter(organization=member.organization).select_related('project','contract_line__contract_version__contract').order_by('-created_at')
    if member.role=='pm':rows=rows.filter(project__manager=member)
    return rows


def form_choices(member):
    member=_fresh(member,True)
    snapshots=approved_line_choices(member)
    return snapshots


def confirmed_acceptance_snapshot(member, acceptance_ref, for_recognition=True):
    member=_fresh(member)
    ref=acceptance_ref if isinstance(acceptance_ref,dict) else None
    if ref and (set(ref)!={'namespace','kind','id','version'} or ref['namespace']!='delivery' or ref['kind']!='acceptance' or type(ref['version']) is not int):raise ValidationError('验收来源无效')
    row=_get(member,ref['id'] if ref else acceptance_ref)
    # Reversal increments workflow version; original approved source stays resolvable.
    approved_version=row.confirmed_version
    if row.status not in {'confirmed','reversed'} or (ref and ref['version'] != approved_version):raise ValidationError('验收尚未独立确认或来源版本无效')
    if for_recognition and row.status!='confirmed':raise ValidationError('已反验收不能确认收入')
    if for_recognition:
        snapshot=approved_line_snapshot(member,row.line_ref,row.confirmed_on)
        if (snapshot['version'].pk!=row.contract_version_id or snapshot['selection'].pk!=row.selection_id or any(snapshot[key]!=getattr(row,field) for key,field in [('digest','approved_digest'),('line_ref','line_ref'),('policy_version','policy_version'),('pricing_configuration','pricing_configuration')])):
            raise ValidationError('验收原合同批准来源或冻结价格已失效')
    if not approved_version or not row.confirmation_audit_id or row.approved_input_snapshot!=_approved_input(row) or row.confirmation_audit.payload.get('approved_digest')!=_digest(row.approved_input_snapshot) or row.confirmation_audit.organization_id!=row.organization_id or row.confirmation_audit.actor_id!=row.confirmed_by_member.user_id or row.confirmation_audit.entity_id!=str(row.pk) or row.confirmation_audit.action!='plugin.delivery.accepted':raise ValidationError('验收冻结输入与真实独立确认审计不一致')
    if not row.confirmed_by_member_id or row.confirmed_by_member_id in {row.created_by_member_id,row.submitted_by_member_id}:raise ValidationError('验收缺少独立确认')
    return {'acceptance':row,'id':str(row.pk),'version':approved_version,'organization':row.organization,'project':row.project,'contract_line':row.contract_line,'contract_line_ref':row.line_ref,'policy_version':row.policy_version,'pricing_configuration':row.pricing_configuration,'accepted_net_cents':row.accepted_net_cents,'confirmed_on':row.confirmed_on,'approved_digest':row.approved_digest,'evidence':row.evidence,'source':{**source_ref(row),'version':approved_version}}


def acceptance_detail(member, acceptance_id):
    row=get_acceptance(member,acceptance_id)
    return {'acceptance':row,'coverages':row.coverages.select_related('milestone'),'evidence':CustomerConfirmationEvidence.objects.filter(pk__in=[x['evidence_id'] for x in row.evidence]),'income':_income(member,row)}


def _approved_input(row):
    return {'organization_id':str(row.organization_id),'project_id':str(row.project_id),'confirmed_at':row.confirmed_at.isoformat() if row.confirmed_at else None,'line_ref':row.line_ref,'contract_version_id':str(row.contract_version_id),'selection_id':str(row.selection_id),'policy_version':row.policy_version,'pricing_configuration':row.pricing_configuration,'approved_digest':row.approved_digest,'evidence':row.evidence,'confirmed_on':row.confirmed_on.isoformat(),'accepted_net_cents':row.accepted_net_cents,'confirmed_version':row.confirmed_version,'confirmed_by_member_id':str(row.confirmed_by_member_id),'created_by_member_id':str(row.created_by_member_id),'submitted_by_member_id':str(row.submitted_by_member_id),'coverages':[{'milestone_id':str(x.milestone_id),'from_basis_points':x.from_basis_points,'to_basis_points':x.to_basis_points,'accepted_net_cents':x.accepted_net_cents} for x in row.coverages.order_by('milestone_id','from_basis_points')]}


def acceptance_projection(member, acceptance_id):
    snapshot=confirmed_acceptance_snapshot(member,acceptance_id,for_recognition=False)
    row=snapshot['acceptance']
    result={'id':str(row.pk),'organization_id':str(row.organization_id),'version':row.confirmed_version,'status':row.status,'created_at':row.created_at.isoformat(),'project_id':str(row.project_id),'contract_line_ref':row.line_ref,'confirmed_on':row.confirmed_on.isoformat(),'confirmed_by_member_id':str(row.confirmed_by_member_id),'evidence':row.evidence,'accepted_net_cents':row.accepted_net_cents,'milestone_coverages':[{'milestone_id':str(x.milestone_id),'from_basis_points':x.from_basis_points,'to_basis_points':x.to_basis_points} for x in row.coverages.all()]}
    if row.replaces_acceptance_id:result['replaces_acceptance_ref']=source_ref(row.replaces_acceptance)
    return result
