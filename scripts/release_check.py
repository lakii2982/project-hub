"""Release metadata checks; actual CI and publication are enforced by the workflow."""
import argparse
import json
from pathlib import Path
import re
import tomllib

def check(root, tag=None):
    version=tomllib.loads((root/'pyproject.toml').read_text())['project']['version']
    if not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)',version):
        raise ValueError('Public release versions must be MAJOR.MINOR.PATCH')
    if tag is not None and tag != 'v'+version:
        raise ValueError('Tag and pyproject.toml version differ')
    changelog=(root/'CHANGELOG.md').read_text()
    if not re.search(r'^##\s+'+re.escape(version)+r'(?:\s|$)',changelog,re.MULTILINE):
        raise ValueError('Release version missing from CHANGELOG')
    readiness=json.loads((root/'project/release-readiness.json').read_text())
    if int(version.split('.')[0]) >= 1:
        required={'lifecycle','financial_rules','production_deployment','postgres_concurrency','backup_restore','agent_client','ai_evaluation','business_acceptance','licenses'}
        if not required <= readiness['gates'].keys(): raise ValueError('1.0 gates missing')
        if any(readiness['gates'][name]['status']!='passed' or not readiness['gates'][name]['evidence'] for name in required):
            raise ValueError('1.0 requires all production and business gates with evidence')
    return {'ok':True,'version':version,'prerelease':version.startswith('0.')}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--tag');args=parser.parse_args()
    try: print(json.dumps(check(Path.cwd(),args.tag)))
    except (ValueError,KeyError,OSError) as error:
        print(json.dumps({'ok':False,'error':str(error)}));raise SystemExit(1)
