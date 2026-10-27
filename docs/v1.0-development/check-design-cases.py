"""Exercise required rejection cases in a disposable copy, never in the checkout."""
from contextlib import redirect_stdout
import types
import io
import json
from pathlib import Path
import shutil
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def main():
    checker = HERE / 'check-design.py'
    module = types.ModuleType('design_checker')
    module.__file__ = str(checker)
    exec(compile(checker.read_text(), str(checker), 'exec'), module.__dict__)
    with tempfile.TemporaryDirectory(prefix='ph130-negative-') as directory:
        root = Path(directory)
        (root / 'docs').mkdir()
        for item in (ROOT / 'docs').iterdir():
            target = root / 'docs' / item.name
            if item.name == 'v1.0-development':
                shutil.copytree(item, target, ignore=shutil.ignore_patterns('__pycache__'))
            elif item.name in {'v1.0-delivery-plan.md', 'v1.0-feature-list.md'}:
                shutil.copy2(item, target)
            else:
                target.symlink_to(item, target_is_directory=item.is_dir())
        (root / 'project').symlink_to(ROOT / 'project', target_is_directory=True)
        (root / 'ROADMAP.md').symlink_to(ROOT / 'ROADMAP.md')
        module.ROOT = root
        module.DESIGN = root / 'docs/v1.0-development'
        registry_path = module.DESIGN / 'work-packages.json'
        trace_path = module.DESIGN / 'requirement-trace.json'
        registry = registry_path.read_bytes()
        trace = trace_path.read_bytes()
        with redirect_stdout(io.StringIO()):
            module.main()  # Reject a broken test fixture before testing mutations.
        cases = ['missing_requirement', 'duplicate_owner', 'dependency_cycle', 'source_hash_mismatch', 'unauthorized_dispatch']
        for case in cases:
            data = json.loads(registry)
            owned = [p for p in data['packages'] if p['requirement_ids']]
            if case == 'missing_requirement':
                owned[0]['requirement_ids'].pop()
            elif case == 'duplicate_owner':
                owned[1]['requirement_ids'].append(owned[0]['requirement_ids'][0])
            elif case == 'dependency_cycle':
                data['packages'][0]['depends_on'].append('PRE-POLICY')
            elif case == 'source_hash_mismatch':
                altered = json.loads(trace)
                altered['product_plan_sha256'] = '0' * 64
                trace_path.write_text(json.dumps(altered))
            else:
                data['packages'][0]['dispatch_allowed'] = True
            registry_path.write_text(json.dumps(data))
            try:
                with redirect_stdout(io.StringIO()):
                    module.main()
            except ValueError as exc:
                expected = {
                    'missing_requirement': 'Requirements missing a primary package',
                    'duplicate_owner': 'Unknown or duplicate primary owner:',
                    'dependency_cycle': 'Dependency cycle at',
                    'source_hash_mismatch': 'Trace snapshot no longer matches',
                    'unauthorized_dispatch': 'Design package became dispatchable:',
                }[case]
                if not str(exc).startswith(expected):
                    raise AssertionError(case + ': rejected for unrelated reason') from exc
                print(case + ': rejected')
            else:
                raise AssertionError(case + ': erroneously accepted')
            finally:
                registry_path.write_bytes(registry)
                trace_path.write_bytes(trace)
        with redirect_stdout(io.StringIO()):
            module.main()
        print('All 5 rejection cases passed; unmodified design still accepted.')


if __name__ == '__main__':
    main()
