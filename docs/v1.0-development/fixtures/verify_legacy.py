#!/usr/bin/env python3
"""Build frozen synthetic legacy database twice in external temp; no live DB input."""
import argparse,hashlib,json,subprocess,sys,tempfile
from pathlib import Path
HERE=Path(__file__).resolve().parent

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repo',type=Path,default=Path.cwd());a=p.parse_args();repo=a.repo.resolve(strict=True)
 root=Path(tempfile.mkdtemp(prefix='ph134-repro-'));runs=[]
 for name in ['run-a','run-b']:
  folder=root/name
  command=[sys.executable,'-B',str(HERE/'build_legacy.py'),'--repo',str(repo),'--output-dir',str(folder)]
  process=subprocess.run(command,capture_output=True,text=True)
  if process.returncode:raise RuntimeError(process.stderr or process.stdout)
  summary=json.loads(process.stdout);commands=json.loads((folder/'commands.json').read_text());assert len(commands)==3 and all(x['returncode']==0 for x in commands)
  runs.append({'run':name,'builder_exit_code':process.returncode,'commands':[{'argv':['python']+x['command'][1:],'exit_code':x['returncode']} for x in commands],'archive_sha256':summary['archive_sha256'],'business_sha256':summary['business_sha256'],'counts':summary['counts'],'total_cost_cents':summary['total_cost_cents'],'integrity':summary['integrity']})
 first=root/'run-a';second=root/'run-b';digest=lambda path:hashlib.sha256(path.read_bytes()).hexdigest();before=digest(first/'legacy.sqlite3')
 refusal=subprocess.run([sys.executable,'-B',str(HERE/'build_legacy.py'),'--repo',str(repo),'--output-dir',str(first)],capture_output=True,text=True)
 assert refusal.returncode!=0 and 'FileExistsError' in refusal.stderr
 assert before==digest(first/'legacy.sqlite3')
 assert (first/'business.json').read_bytes()==(second/'business.json').read_bytes()
 for row in runs:
  assert row['total_cost_cents']==58 and row['integrity']=={'ok':True,'errors':[],'organizations':1,'entries':4}
 result={'kind':'actual_frozen_synthetic_preparation_checks_not_product_uat','result':'pass','python':sys.version,'baseline_sha':'119a04916e3e96a5e4d86516a5f13e841faf2f67','input_sha256':{name:digest(HERE/name) for name in ['build_legacy.py','seed_legacy.py','verify_legacy.py']},'frozen_source_no_replace_objects':True,'archive_digest_checked':True,'runs':runs,'business_bytes_identical':True,'original_lines_cents':[2,2,2,52],'existing_output_refused':True,'existing_database_hash_unchanged':True,'output_location':'external temporary directory; retained for local inspection only; no database copied to source','not_verified':['new migrations or upgrade/cutover','PostgreSQL multi-connection','Linux database+attachment restore','product UAT','browser UI']}
 print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
