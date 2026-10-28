#!/usr/bin/env python3
"""Offline fixture structure/arithmetic/hash checks. Never imports product or reads DB."""
from pathlib import Path
from decimal import Decimal, ROUND_FLOOR
import argparse,csv,hashlib,json,importlib.util,sys

HERE=Path(__file__).resolve().parent

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--contracts',type=Path,default=HERE.parent/'contracts');args=p.parse_args()
 f=json.loads((HERE/'fixture.json').read_text());samples=json.loads((HERE/'command-samples.json').read_text());coverage=json.loads((HERE/'requirements-coverage.json').read_text()); errors=[]
 def check(ok,msg):
  if not ok:errors.append(msg)
 check(f['status']=='preparation_fixture_only','classification')
 check(len(f['enterprises'])==2 and set(f['primary_roles'])=={'owner','finance','pm','member'},'two enterprises/four primary roles')
 check({p['template'] for p in f['projects']} >= {'fixed_milestone','confirmed_time','fixed_service_period'},'three charging templates')
 check(len(f['bases'])==18 and len({b['id'] for b in f['bases']})==18,'18 independent bases')
 registry={x['label']:x for x in f['source_registry']};check(len(registry)==len(f['source_registry']),'source ids unique')
 sample_ids={x['id'] for x in samples['samples']};check(len(sample_ids)==len(samples['samples']),'sample ids unique')
 files={}
 for item in f['files']:
  path=HERE/item['path'];check(path.is_relative_to(HERE) and path.is_file(),item['id']+' exists')
  if path.is_file():
   raw=path.read_bytes();check(bool(raw),item['id']+' nonempty');check(hashlib.sha256(raw).hexdigest()==item['sha256'],item['id']+' hash')
   if item['kind']=='csv':files[item['id']]=list(csv.DictReader(raw.decode().splitlines()))
 check(files['CSV-AP-NORMAL']==files['CSV-AP-REORDERED'],'CSV reordered normalized equality')
 check(files['CSV-AP-NORMAL']!=files['CSV-AP-CONFLICT'],'CSV changed effective contents')
 for u in f['uat_sequences']:
  check(u['execution_status']=='not_run',u['id']+' not_run')
  check(u['base_id']=='BASE-'+u['id'] and u['rejection_cases'],u['id']+' independent base/rejections')
  for stage in u['steps']:
   check(set(stage['expected'])==set(f['scenario_policy']['state_keys']),stage['stage_id']+' state keys')
   check(all(type(x)is int or x is None for x in stage['expected'].values()),stage['stage_id']+' strict integer oracle')
   check(set(stage.get('command_sample_ids',[]))<=sample_ids,stage['stage_id']+' command sample links')
   check(bool(stage['command_sample_ids']) or bool(stage.get('procedural_operation')),stage['stage_id']+' binding/procedure')
   check(set(stage['source_ids'])<=set(registry),stage['stage_id']+' declared source refs')
  check(set(u['negative'])<=set(f['rejection_catalog']),u['id']+' rejection catalog')
 check([u['id'] for u in f['uat_sequences']]==['UAT-%02d'%i for i in range(1,19)],'UAT ids')
 historical=files['CSV-HIST-COST'][0];opening=f['facts']['opening']
 check(historical['source_date']<=opening['cutoff_date'] and int(historical['amount_minor'])<=opening['legacy_cost_cents'],'historical CSV is inside opening coverage')
 check(f['uat_sequences'][11]['steps'][1]['source_ids']==['OPEN-20260831','CSV-HIST-COST'],'opening overlap binds historical sample')
 sample_values={x['id']:x['value'] for x in samples['samples']}
 check(sample_values['SET-U10-RET']['payload']['fields']['due_date']=='2027-10-31','retention due date matches UAT10 maturity')
 future=f['uat_sequences'][2]['steps'][-1]
 check(future['expected']['remaining_forecast_magnitude_cents']==0 and future['forecast_components']['later_unsettled_conditional_cents']==53000 and future['forecast_components']['combined_remaining_magnitude_cents']==53000,'UAT03 settled tranche vs remaining contract coverage')
 race=f['uat_sequences'][13]['steps'][1];by_sample={x['id']:x['value'] for x in samples['samples']}
 close=by_sample['PERIOD-CLOSE']['payload'];post=by_sample['PERIOD-RACE-SEP-POST']['payload']
 check(close['period']==post['posting_period']==post['occurred_on'][:7]=='2026-09','period race same month')
 check(race['command_sample_ids']==['PERIOD-CLOSE','PERIOD-RACE-SEP-POST'] and race['oracle_inputs']['scheduled_winner']=='close','period close-wins barrier and sample binding')
 check(race['expected']['company_cost_cents']==0 and race['alternative_branches'][0]['expected']['company_cost_cents']==10000,'period race branch oracles')
 fact=f['facts'];lab=fact['labor'];check(sum(lab['allocations'].values())==lab['pool_cents'],'salary conservation')
 check(Decimal(lab['pool_cents'])*600/lab['capacity_minutes']==150000,'salary project oracle')
 check(f['uat_sequences'][8]['steps'][1]['expected']['net_cash_movement_cents']==sum([31800,-10000,10000,-3000]),'UAT09 cumulative cash')
 for key in ('main','rounding_special'):
  shared=fact['shared'][key]
  for k in ('initial','reallocation','equal'):
   if k in shared:check(sum(shared[k].values())==shared['amount_cents'],'shared '+key+k)
 check(fact['tax_unknown']['gross_cents']-fact['tax_unknown']['deductible_tax_cents']==fact['tax_unknown']['final_cost_cents'],'tax adjustment')
 check(fact['annual_prepaid']['month_one_cost_cents']+fact['annual_prepaid']['remaining_cents']==fact['annual_prepaid']['paid_cents'],'prepaid consumption')
 # Independent Decimal math for golden monthly remainder example; not a product engine.
 ideal=[Decimal(101)*x/60 for x in [3,31,26]];alloc=[int(x.to_integral_value(rounding=ROUND_FLOOR)) for x in ideal]
 for i in sorted(range(3),key=lambda i: (-(ideal[i]-alloc[i]),i))[:101-sum(alloc)]:alloc[i]+=1
 check(alloc==[5,52,44] and sum([2,2,2,52])==58,'new group vs old row rounding')
 rows=coverage['requirements'];check(len(rows)==137 and len({r['requirement_id'] for r in rows})==137,'137 requirements')
 check(all(r['specialized_case_id'] and r['acceptance'] and r['execution_status']=='not_run' for r in rows),'requirement-specific cases')
 # Load authoritative strict validator without running its main or accessing network.
 path=args.contracts/'validate_contracts.py';spec=importlib.util.spec_from_file_location('ph134_contract_validation',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 schemas,reg=module.load()
 def validate(sample):
  schema=schemas[sample['schema']]
  if sample.get('definition'):schema={'$ref':schema['$id']+'#/$defs/'+sample['definition']}
  module.validator(schema,reg).validate(sample['value'])
 for sample in samples['samples']:
  try:validate(sample)
  except Exception as exc:errors.append(sample['id']+': '+str(exc)[:600])
 from jsonschema import ValidationError
 for sample in samples['negative_schema_samples']:
  try:validate(sample)
  except ValidationError:pass
  else:errors.append(sample['id']+': negative schema specimen accepted')
 for row in registry.values():
  try:module.validator({'$ref':schemas['contract-bundle.schema.json']['$id']+'#/$defs/SourceRef'},reg).validate(row['source'])
  except Exception as exc:errors.append(row['label']+': '+str(exc)[:200])
 for name,digest in f['contract_artifact_sha256'].items():check(hashlib.sha256((args.contracts/name).read_bytes()).hexdigest()==digest,'frozen contract hash '+name)
 check(hashlib.sha256((args.contracts.parent.parent/'v1.0-delivery-plan.md').read_bytes()).hexdigest()==coverage['product_plan_sha256'],'product plan hash')
 result={'result':'fail' if errors else 'pass','kind':'fixture_structure_hash_arithmetic_and_schema_only','positive_schema_samples':len(samples['samples']),'negative_schema_samples':len(samples['negative_schema_samples']),'sources':len(registry),'uat_sequences':18,'requirements':len(rows),'product_commands_executed':False,'product_uat_passed':False,'database_read':False,'input_sha256':{name:hashlib.sha256((HERE/name).read_bytes()).hexdigest() for name in ['fixture.json','command-samples.json','requirements-coverage.json','check_fixture.py']},'errors':errors}
 print(json.dumps(result,ensure_ascii=False,indent=2));return bool(errors)

if __name__=='__main__':sys.exit(main())
