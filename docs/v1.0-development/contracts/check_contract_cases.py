"""Real schema boundary examples; no database or product command is executed."""
from copy import deepcopy
from pathlib import Path
import json
import types
from jsonschema import ValidationError

HERE=Path(__file__).resolve().parent
module=types.ModuleType('contract_validation');module.__file__=str(HERE/'validate_contracts.py')
exec(compile((HERE/'validate_contracts.py').read_text(),module.__file__,'exec'),module.__dict__)
SCHEMAS,REGISTRY=module.load()


def validate(file,value,definition=None):
    schema=SCHEMAS[file]
    if definition is not None:
        pointer=definition.replace('~','~0').replace('/','~1')
        schema={'$ref':schema['$id']+'#/$defs/'+pointer}
    module.validator(schema,REGISTRY).validate(value)


def source(namespace,kind):return {'namespace':namespace,'kind':kind,'id':1,'version':1}
def command(name,payload):return {'contract':name,'request_id':1,'idempotency_key':'00000000','payload':payload}


def main():
    passed=[]
    def reject(name,file,value,definition=None):
        try:validate(file,value,definition)
        except ValidationError:passed.append(name)
        else:raise AssertionError(name+': unexpectedly accepted')
    cash=command('cash.transaction.record/v1',{'funding_account_id':1,'transaction_date':'2026-10-01','direction':'out','gross_cents':101,'external_source_key':'00000000','payee':{'kind':'employee','id':1}})
    validate('commands.schema.json',cash)
    assert 'dry_run'not in cash  # Schema annotations do not inject server defaults.
    for value,label in [(True,'bool_money'),(101.0,'integral_float_money'),(101.5,'fraction_money'),(10**12+1,'money_cap')]:
        bad=deepcopy(cash);bad['payload']['gross_cents']=value;reject(label,'commands.schema.json',bad)
    bad=deepcopy(cash);bad['organization_id']=2;reject('caller_tenant','commands.schema.json',bad)
    bad=deepcopy(cash);bad['payload']['approved_by_member_id']=1;reject('caller_approver','commands.schema.json',bad)
    revenue=command('management_revenue.recognize/v1',{'contract_line_ref':source('contracts','contract_line'),'performance_ref':source('delivery','acceptance'),'policy_version':{'policy_id':1,'version':1}})
    validate('commands.schema.json',revenue)
    bad=deepcopy(revenue);bad['payload']['recognized_net_cents']=100;reject('raw_income','commands.schema.json',bad)
    for field in ['contract_line_ref','performance_ref']:
        bad=deepcopy(revenue);bad['payload'][field]=source('cash','cash_transaction');reject('wrong_kind_'+field,'commands.schema.json',bad)
    bad=deepcopy(revenue);bad['payload']['policy_version']['approved_by_member_id']=1;reject('nested_approval','commands.schema.json',bad)
    pricing={'template':'fixed_milestone','policy_ref':{'policy_id':1,'version':1},'currency':'CNY','confirmed_by_customer_required':True,'net_cap_cents':100,'milestones':[{'milestone_id':1,'allocated_net_cents':100}]}
    line={'line_no':1,'net_cents':100,'tax_cents':0,'gross_cents':100,'pricing_configuration':pricing}
    validate('write-dtos.schema.json',line,'ContractLine')
    for field in ['organization_id','project_id','status','approved_by_member_id']:
        bad=deepcopy(line);bad[field]=1;reject('nested_contract_'+field,'write-dtos.schema.json',bad,'ContractLine')
    bad=source('cash','timesheet');reject('wrong_namespace_kind','contract-bundle.schema.json',bad,'SourceRef')
    for alias,target in [('TimesheetBillableConfirmation','BillableConfirmation'),('CostAllocation','AllocationTarget'),('AdvanceOffset','Offset')]:
        assert SCHEMAS['entities.schema.json']['$defs'][alias]['$ref']=='#/$defs/'+target
        schema=SCHEMAS['entities.schema.json']['$defs'][target]['properties']['status']
        module.validator(schema,REGISTRY).validate(schema['enum'][0])
        try:module.validator(schema,REGISTRY).validate('evil')
        except ValidationError:passed.append('status_'+alias)
        else:raise AssertionError('Unbounded status '+alias)
    query={'contract':'contracts.effective_line.get/v1','request_id':1,'query':{'source':source('contracts','contract_line'),'as_of_date':'2026-10-01'}}
    validate('queries.schema.json',query)
    bad=deepcopy(query);bad['query'].pop('source');reject('query_missing_selector','queries.schema.json',bad)
    bad=deepcopy(query);bad['query']['source']=source('cash','cash_transaction');reject('query_wrong_source','queries.schema.json',bad)
    salary_components=[{'component_code':'salary','amount_cents':101,'evidence':[{'evidence_id':1,'sha256':'0'*64}]}]
    for capacity in ['missing',0,None]:
        facts={'salary_components':salary_components}
        if capacity!='missing':facts['capacity_minutes']=capacity
        value=command('costing.salary_pool.draft.save/v1',{'fields':{'employee_id':1,'period':'2026-10','allocation_inputs':facts}})
        validate('commands.schema.json',value)
    snapshot={'method':'monthly_comprehensive_capacity_v1','effective_from':'2026-10-01','salary_components':salary_components,'valuation_state':'reconciled_actual','covered_through':'2026-10-01','capacity_minutes':60,'approved_inputs':[],'unallocated_destination_id':2,'rounding_group':'organization+employee+month+method_version+policy_version+currency','algorithm':'monthly_group_half_up_largest_remainder_v1','customer_policy_ref':{'policy_id':1,'version':1}}
    validate('contract-bundle.schema.json',snapshot,'MonthlyLaborInputs')
    for capacity in ['missing',0,None]:
        bad=deepcopy(snapshot)
        if capacity=='missing':bad.pop('capacity_minutes')
        else:bad['capacity_minutes']=capacity
        reject('final_labor_capacity_'+str(capacity),'contract-bundle.schema.json',bad,'MonthlyLaborInputs')
    entry={'book':'cost','scope':{'kind':'nonproject','id':1},'amount_cents':101}
    validate('write-dtos.schema.json',entry,'PostingEntryInput')
    bank={'book':'cash_in','scope':{'kind':'funding_account','id':1},'amount_cents':101}
    validate('write-dtos.schema.json',bank,'PostingEntryInput')
    for label,book,scope in [('unknown_book','profit','project'),('cash_project','cash_in','project'),('new_legacy_cash','cash_out','legacy_project'),('cost_bank','cost','funding_account')]:
        bad={'book':book,'scope':{'kind':scope,'id':1},'amount_cents':101}
        reject(label,'write-dtos.schema.json',bad,'PostingEntryInput')
    bad=deepcopy(entry);bad['amount_cents']=101.0;reject('posting_float','write-dtos.schema.json',bad,'PostingEntryInput')
    bad=deepcopy(entry);bad['organization_id']=2;reject('posting_caller_org','write-dtos.schema.json',bad,'PostingEntryInput')
    pool={'id':1,'organization_id':1,'version':1,'status':'approved','created_at':'2026-10-01T00:00:00Z','employee_id':1,'period':'2026-10','total_comprehensive_cost_cents':101,'salary_components':salary_components,'allocation_inputs':None,'allocation_state':'unallocated','valuation_state':'reconciled_actual','covered_through':'2026-10-01','policy_version':{'policy_id':1,'version':1,'status':'approved','effective_from':'2026-10-01'}}
    validate('entities.schema.json',pool,'SalaryPool')
    legacy={'id':1,'organization_id':1,'version':1,'status':'posted','created_at':'2026-10-01T00:00:00Z','origin':'legacy','legacy_source_type':'document','legacy_source_id':1,'accounting_event_id':None,'book':'cash_out','amount_cents':101,'scope':{'kind':'legacy_project','id':1}}
    validate('entities.schema.json',legacy,'LedgerPosting')
    bad=deepcopy(legacy);bad['accounting_event_id']=1;reject('fabricated_legacy_event','entities.schema.json',bad,'LedgerPosting')
    bad=deepcopy(legacy);bad['origin']='current';reject('new_posting_missing_event','entities.schema.json',bad,'LedgerPosting')
    print(json.dumps({'result':'pass','kind':'schema_examples_only','positive_examples':12,'rejected_examples':len(passed),'cases':passed,'server_defaults_not_applied_by_schema':True,'business_commands_executed':False}))


if __name__=='__main__':main()
