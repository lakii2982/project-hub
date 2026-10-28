"""Compile unapplied PH137 design patches in memory; never writes canonical files."""
import contextlib
import copy
import io
import hashlib
import json
from pathlib import Path
import types
from jsonschema import ValidationError

HERE=Path(__file__).resolve().parent
CONTRACTS=HERE.parent/'contracts'

def keys(path):
    return [x.replace('~1','/').replace('~0','~') for x in path.lstrip('/').split('/')]

def lookup(data,path):
    if not path or path=='#':return data
    for key in keys(path.removeprefix('#')):
        data=data[int(key)] if isinstance(data,list) else data[key]
    return data

def patch(data,op):
    parts=keys(op['path']);parent=data
    for key in parts[:-1]:parent=parent[int(key)] if isinstance(parent,list) else parent[key]
    key=parts[-1];value=copy.deepcopy(op.get('value'))
    if op['op']=='test':
        assert lookup(data,op['path'])==value,op['path'];return
    assert op['op'] in ['add','replace','remove'],op['op']
    if isinstance(parent,list):
        index=len(parent) if key=='-' else int(key)
        if op['op']=='add':parent.insert(index,value)
        elif op['op']=='remove':parent.pop(index)
        else:parent[index]=value
    elif op['op']=='remove':del parent[key]
    else:
        if op['op']=='replace':assert key in parent,op['path']
        parent[key]=value

def expanded(value,plan):
    if isinstance(value,dict):
        if set(value)=={'$plan_ref'}:
            assert value['$plan_ref'].startswith('#/fragments/')
            return expanded(copy.deepcopy(lookup(plan,value['$plan_ref'])),plan)
        return {k:expanded(v,plan) for k,v in value.items()}
    if isinstance(value,list):return [expanded(v,plan) for v in value]
    return value

def compile_plans(base_documents=None):
    docs=copy.deepcopy(base_documents) if base_documents is not None else {p.name:json.loads(p.read_text()) for p in CONTRACTS.glob('*.json')}
    freeze_file=HERE/'extension-freeze.json'
    if freeze_file.exists() and base_documents is None:
        frozen=json.loads(freeze_file.read_text())
        for name,digest in frozen['canonical_sha256'].items():
            assert hashlib.sha256((CONTRACTS/name).read_bytes()).hexdigest()==digest,'Materialized contract changed: '+name
        for name,digest in frozen['input_sha256'].items():
            assert hashlib.sha256((HERE/name).read_bytes()).hexdigest()==digest,'Frozen patch input changed: '+name
        return docs,frozen['patch_entries']
    operations=0
    for name in ['delivery','cash','invoice','bank-entry']:
        plan=json.loads((HERE/(name+'-extension.plan.json')).read_text())
        for raw in plan.get('patches',plan.get('schema_patch_blueprint',[])):
            raw=expanded(raw,plan);filename=Path(raw['file']).name
            assert filename in docs,filename
            op=raw.get('patch') or {k:v for k,v in raw.items() if k in ['op','path','value','from']}
            patch(docs[filename],op);operations+=1
    root=json.loads((HERE/'core-extension.plan.json').read_text())
    for filename,defs in root['definitions'].items():
        for name,schema in defs.items():
            assert name not in docs[filename]['$defs'],name
            docs[filename]['$defs'][name]=copy.deepcopy(schema)
            if filename in ['commands.schema.json','command-results.schema.json','events.schema.json']:
                docs[filename]['oneOf'].append({'$ref':'#/$defs/'+name.replace('~','~0').replace('/','~1')})
            operations+=1
    for name,entry in root['source_registry_additions'].items():
        assert name not in docs['source-registry.json']['objects'],name
        docs['source-registry.json']['objects'][name]=entry
    for name,entry in root.get('relationship_additions',{}).items():
        assert name not in docs['relationships.json']['relationships'],name
        docs['relationships.json']['relationships'][name]=entry
    for cid,additions in root['catalog_additions'].items():
        row=next(r for r in docs['contract-catalog.json']['contract_catalog'] if r['id']==cid)
        for field,values in additions.items():
            for value in values:
                if value not in row[field]:row[field].append(value)
    for op in root['targeted_patches']:
        if op['operation']=='append_unique':
            target=lookup(docs[op['file']],op['pointer'])
            for value in op['value']:
                if value not in target:target.append(value)
        else:patch(docs[op['file']],{'op':op['operation'],'path':op['pointer'],'value':op['value']})
        operations+=1
    # Extend only the explicit generated object-name inventory; no arbitrary types/defaults.
    entity_names=list(docs['source-registry.json']['objects'])
    def inventories(value):
        if isinstance(value,dict):
            if isinstance(value.get('enum'),list) and len(value['enum'])>70 and {'Membership','Project','AccountingEvent'}<=set(value['enum']):
                for name in entity_names:
                    if name not in value['enum']:value['enum'].append(name)
            for child in value.values():inventories(child)
        elif isinstance(value,list):
            for child in value:inventories(child)
    inventories(docs)
    for filename,field in [('commands.schema.json','x-command-count'),('events.schema.json','x-event-count'),('queries.schema.json','x-query-count')]:
        if field in docs[filename]:docs[filename][field]=len(docs[filename]['$defs'])
    return docs,operations

def main():
    docs,operations=compile_plans()
    module=types.ModuleType('validation');module.__file__=str(CONTRACTS/'validate_contracts.py')
    exec(compile(Path(module.__file__).read_text(),module.__file__,'exec'),module.__dict__)
    schemas={n:v for n,v in docs.items() if n.endswith('.schema.json')}
    registry=module.offline_registry(schemas);refs=0
    for name,schema in schemas.items():
        module.Draft202012Validator.check_schema(schema)
        resolver=registry.resolver(base_uri=schema['$id'])
        for reference in module.walk_refs(schema):resolver.lookup(reference);refs+=1
    module.validator(schemas['contract-bundle.schema.json'],registry).validate(docs['contract-catalog.json'])
    for field,file in [('commands','commands.schema.json'),('queries','queries.schema.json'),('events','events.schema.json')]:
        names=[n for r in docs['contract-catalog.json']['contract_catalog'] for n in r[field]]
        assert len(names)==len(set(names)) and set(names)==set(schemas[file]['$defs']),field
    assert set(schemas['commands.schema.json']['$defs'])==set(schemas['command-results.schema.json']['$defs'])
    sources=docs['source-registry.json']['objects']
    pairs=[(x['properties']['namespace']['const'],x['properties']['kind']['const']) for x in schemas['contract-bundle.schema.json']['$defs']['SourceRef']['oneOf']]
    assert len(pairs)==len(set(pairs))
    assert set(pairs)=={(r['namespace'],r['kind']) for r in sources.values()},'SourceRef and owners differ'
    for value in docs['relationships.json']['relationships'].values():
        assert value['canonical_record'] in sources and sources[value['canonical_record']]['owner']==value['owner']
    bridges=[n for row in docs['contract-catalog.json']['contract_catalog'] for n in row['bridges']]
    assert len(bridges)==len(set(bridges)) and set(bridges)==set(docs['relationships.json']['relationships']),'Bridge catalog differs'
    cases=types.ModuleType('cases');cases.__file__=str(CONTRACTS/'check_contract_cases.py')
    exec(compile(Path(cases.__file__).read_text(),cases.__file__,'exec'),cases.__dict__)
    cases.SCHEMAS=schemas;cases.REGISTRY=registry
    capture=io.StringIO()
    with contextlib.redirect_stdout(capture):cases.main()
    old_cases=json.loads(capture.getvalue())
    samples=json.loads((HERE.parent/'fixtures/command-samples.json').read_text())
    def validate_sample(row):
        schema=schemas[row['schema']]
        definition=row.get('definition')
        if not definition and isinstance(row['value'],dict):
            name=row['value'].get('contract')
            if name in schema.get('$defs',{}):definition=name
        if definition:
            schema={'$ref':schema['$id']+'#/$defs/'+definition.replace('~','~0').replace('/','~1')}
        module.validator(schema,registry).validate(row['value'])
    for row in samples['samples']:validate_sample(row)
    for row in samples['negative_schema_samples']:
        try:validate_sample(row)
        except ValidationError:pass
        else:raise AssertionError('Old negative accepted: '+row['id'])
    peer_invoice=json.loads((HERE/'invoice-extension.plan.json').read_text())['synthetic_examples']
    for row in peer_invoice['schema_valid']:
        file,definition=row['schema'].split('#/$defs/')
        validate_sample({'schema':file,'definition':definition.replace('~1','/'),'value':row['value']})
    for case in peer_invoice['schema_invalid']:
        value=copy.deepcopy(peer_invoice['schema_valid'][0]['value'])
        for op in case['patch']:patch(value,op)
        try:validate_sample({'schema':'commands.schema.json','value':value})
        except ValidationError:pass
        else:raise AssertionError('Invoice negative accepted: '+case['reason'])
    bank=json.loads((HERE/'bank-entry-extension.plan.json').read_text())
    for value in bank['synthetic_schema_examples']:
        validate_sample({'schema':'commands.schema.json','value':value})
    for value in bank.get('synthetic_result_examples',[]):
        validate_sample({'schema':'command-results.schema.json','value':value})
    supplemental=json.loads((HERE/'extension-schema-samples.json').read_text())
    for row in supplemental['positive']:
        try:validate_sample(row)
        except ValidationError as exc:raise AssertionError('New positive '+row['id']+': '+exc.message[:500]) from None
    for row in supplemental['negative']:
        try:validate_sample(row)
        except ValidationError:pass
        else:raise AssertionError('New negative accepted: '+row['id'])
    print(json.dumps({'scope':'design_contracts_only','input_state':'materialized' if (HERE/'extension-freeze.json').exists() else 'unapplied','patch_entries':operations,'resolved_refs':refs,
        'commands':len(schemas['commands.schema.json']['$defs']),'events':len(schemas['events.schema.json']['$defs']),
        'old_positive':old_cases['positive_examples'],'old_rejected':old_cases['rejected_examples'],
        'old_fixture_positive':len(samples['samples']),'old_fixture_rejected':len(samples['negative_schema_samples']),
        'bank_entry_positive':len(bank['synthetic_schema_examples']),
        'new_invoice_positive':len(peer_invoice['schema_valid']),'new_invoice_rejected':len(peer_invoice['schema_invalid']),
        'new_command_positive':len(supplemental['positive']),'new_command_rejected':len(supplemental['negative']),
        'canonical_files_written':False,'runtime_business_verified':False}))

if __name__=='__main__':main()
