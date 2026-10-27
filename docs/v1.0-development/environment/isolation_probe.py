"""Real macOS isolation qualification with synthetic, disposable sentinels only."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile


def main():
    from tooling.ai_delivery.execution.sandbox import sandbox_argv
    with tempfile.TemporaryDirectory(prefix='ph131-isolation-') as directory:
        root=Path(directory).resolve()
        worktree=root/'worktree'; runtime=root/'runtime'; outside=root/'controller'
        for path in (worktree,runtime,outside): path.mkdir(mode=0o700)
        (worktree/'readable.txt').write_text('SYNTHETIC_WORKER_INPUT')
        (outside/'sentinel.txt').write_text('SYNTHETIC_CONTROLLER_INPUT')
        code='''from pathlib import Path
import errno,json,socket,sys
worktree,runtime,outside=map(Path,sys.argv[1:])
assert (worktree/'readable.txt').read_text()=='SYNTHETIC_WORKER_INPUT'
checks={}
for label,operation in [
 ('controller_read',lambda:(outside/'sentinel.txt').read_text()),
 ('controller_write',lambda:(outside/'forbidden.txt').write_text('synthetic')),
 ('review_worktree_write',lambda:(worktree/'forbidden.txt').write_text('synthetic')),
 ('network',lambda:socket.create_connection(('127.0.0.1',9),timeout=1)),
]:
 try: operation()
 except OSError as exc:
  assert exc.errno in (errno.EPERM,errno.EACCES),(label,type(exc).__name__,exc.errno)
  checks[label]='denied_by_os'
 else: raise AssertionError(label+': unexpectedly allowed')
(runtime/'allowed.txt').write_text('SYNTHETIC_RUNTIME_OUTPUT')
checks['runtime_write']='allowed'
print(json.dumps(checks,sort_keys=True))
'''
        argv=sandbox_argv([sys.executable,'-B','-c',code,str(worktree),str(runtime),str(outside)],
            worktree=worktree,runtime=runtime,network=False,readonly=True,
            read_roots=[Path(sys.prefix),Path(sys.base_prefix)])
        result=subprocess.run(argv,cwd=worktree,env={'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1'},
            capture_output=True,text=True,timeout=15)
        if result.returncode:
            raise RuntimeError('isolation_probe_failed: '+result.stderr[-1800:])
        assert not (worktree/'forbidden.txt').exists() and not (outside/'forbidden.txt').exists()
        print(json.dumps({'result':'pass','kind':'real_os_sandbox_synthetic_probe','checks':json.loads(result.stdout)},sort_keys=True))


if __name__=='__main__':
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
    main()
