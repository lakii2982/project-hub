"""Offline, strict-integer validation of preparation contracts; grants no authority."""
from pathlib import Path
import json
import hashlib
import re
import contextlib
import io
import runpy
from jsonschema import Draft202012Validator, FormatChecker, validators
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource

HERE=Path(__file__).resolve().parent
StrictValidator=validators.extend(Draft202012Validator,type_checker=Draft202012Validator.TYPE_CHECKER.redefine('integer',lambda checker,value:type(value) is int))


def offline_registry(schemas):
    def deny(uri):
        raise NoSuchResource(ref=uri)
    return Registry(retrieve=deny).with_resources((s['$id'],Resource.from_contents(s))for s in schemas.values())


def load():
    schemas={p.name:json.loads(p.read_text())for p in sorted(HERE.glob('*.schema.json'))}
    return schemas,offline_registry(schemas)


def validator(schema,registry):
    return StrictValidator(schema,registry=registry,format_checker=FormatChecker())


def walk_refs(value):
    if isinstance(value,dict):
        if '$ref'in value:yield value['$ref']
        for nested in value.values():yield from walk_refs(nested)
    elif isinstance(value,list):
        for nested in value:yield from walk_refs(nested)


def main():
    schemas,registry=load();count=0
    for filename,schema in schemas.items():
        Draft202012Validator.check_schema(schema)
        resolver=registry.resolver(base_uri=schema['$id'])
        for reference in walk_refs(schema):
            try:resolver.lookup(reference)
            except Exception as exc:raise AssertionError(filename+': unresolved '+reference)from exc
            count+=1
    catalog=json.loads((HERE/'contract-catalog.json').read_text())
    validator(schemas['contract-bundle.schema.json'],registry).validate(catalog)
    source=json.loads((HERE/'source-registry.json').read_text())['objects']
    assert len({(r['namespace'],r['kind'])for r in source.values()})==len(source),'ambiguous canonical source'
    entities=schemas['entities.schema.json']['$defs']
    objects={name for c in catalog['contract_catalog']for name in c['objects']}
    assert objects<=set(entities),'catalog object missing actual schema'
    coverage=json.loads((HERE/'requirements-coverage.json').read_text())
    assert coverage['product_plan_sha256']==hashlib.sha256((HERE.parent.parent/'v1.0-delivery-plan.md').read_bytes()).hexdigest(),'product snapshot drift'
    rows=coverage['requirements']
    relationships=json.loads((HERE/'relationships.json').read_text())['relationships']
    bridge_names=[name for c in catalog['contract_catalog']for name in c['bridges']]
    assert len(bridge_names)==len(set(bridge_names))and set(bridge_names)==set(relationships),'bridge ownership mismatch'
    for row in relationships.values():
        name=row['canonical_record']
        assert name in entities and row['owner']==source[name]['owner'],'relationship has competing owner'
        registry.resolver(base_uri=schemas['entities.schema.json']['$id']).lookup(row['schema_ref'])
    trace=json.loads((HERE.parent/'requirement-trace.json').read_text())['requirements']
    assert len(rows)==137 and len({r['id']for r in rows})==137
    assert {r['id']for r in rows}=={r['requirement_id']for r in trace}
    assert all(set(r['target_objects'])<=set(entities)for r in rows),'requirements reference undefined object'
    for field,file in [('commands','commands.schema.json'),('queries','queries.schema.json'),('events','events.schema.json')]:
        names=[n for c in catalog['contract_catalog']for n in c[field]]
        assert len(names)==len(set(names)),field+' repeated ownership'
        assert set(names)==set(schemas[file]['$defs']),field+' catalog mismatch'
    assert set(schemas['commands.schema.json']['$defs'])==set(schemas['command-results.schema.json']['$defs'])
    assert set(schemas['queries.schema.json']['$defs'])==set(schemas['query-results.schema.json']['$defs'])
    supplemental=None
    extension=HERE.parent/'promotion-draft/check-extension-plans.py'
    if (extension.parent/'extension-freeze.json').exists():
        capture=io.StringIO()
        with contextlib.redirect_stdout(capture):runpy.run_path(str(extension),run_name='__main__')
        supplemental=json.loads(capture.getvalue())
    print(json.dumps({'result':'pass','kind':'offline_preparation_schema_only','schemas':len(schemas),'resolved_refs':count,'requirements':137,'commands':len(schemas['commands.schema.json']['$defs']),'queries':len(schemas['queries.schema.json']['$defs']),'events':len(schemas['events.schema.json']['$defs']),'strict_python_integer':True,'supplemental_design_examples':supplemental,'runtime_business_verified':False}))


if __name__=='__main__':main()
