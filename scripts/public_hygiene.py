"""Check tracked publication boundaries. Secret content scanning is done by Gitleaks."""
import json
from pathlib import Path, PurePosixPath
import subprocess

BLOCKED = {'runtime', 'backups', 'releases', '.venv', '.agent-venv', 'node_modules', '__pycache__', '.cache', 'staticfiles'}

def inspect_paths(paths):
    errors=[]
    for value in paths:
        path=PurePosixPath(value)
        if path.is_absolute() or '..' in path.parts or set(path.parts) & BLOCKED:
            errors.append({'path':value,'reason':'private/runtime path'})
        elif path.name.startswith('.env') and value != '.env.example':
            errors.append({'path':value,'reason':'environment file'})
        elif path.suffix.lower() in {'.sqlite3','.db','.dump','.pem','.key','.p12','.pyc'}:
            errors.append({'path':value,'reason':'database/credential/cache artifact'})
    return errors

if __name__=='__main__':
    result=subprocess.run(['git','ls-files','-z'],check=True,capture_output=True)
    names=[x for x in result.stdout.decode().split('\0') if x]
    errors=inspect_paths(names)
    for name in names:
        path=Path(name)
        if path.is_symlink(): errors.append({'path':name,'reason':'symlink must not be published'})
    print(json.dumps({'ok':not errors,'tracked_files':len(names),'errors':errors},ensure_ascii=False))
    raise SystemExit(bool(errors))
