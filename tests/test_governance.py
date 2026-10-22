import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import tomllib

ROOT=Path(__file__).resolve().parents[1]
def load(name, path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module
hygiene=load('public_hygiene','scripts/public_hygiene.py')
release=load('release_check','scripts/release_check.py')

class GovernanceTests(unittest.TestCase):
    def test_private_runtime_and_credentials_are_rejected(self):
        paths=['runtime/pmo.sqlite3','backups/database.dump','.env','integrations/mcp/.env','.venv/file','.cache/tool','key.pem']
        self.assertEqual({x['path'] for x in hygiene.inspect_paths(paths)},set(paths))
        self.assertEqual(hygiene.inspect_paths(['.env.example','core/models.py','LICENSE','project/tasks.json']),[])

    def fixture(self, directory, version):
        root=Path(directory)
        (root/'project').mkdir()
        (root/'pyproject.toml').write_text('[project]\nversion="'+version+'"\n')
        (root/'CHANGELOG.md').write_text('## '+version+'\n')
        (root/'project/release-readiness.json').write_text(json.dumps({'gates':{}}))
        return root

    def test_tag_must_match_single_version_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory,'0.1.0')
            self.assertTrue(release.check(root,'v0.1.0')['prerelease'])
            with self.assertRaises(ValueError): release.check(root,'v0.2.0')

    def test_production_version_cannot_skip_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory,'1.0.0')
            with self.assertRaises(ValueError): release.check(root,'v1.0.0')

    def test_health_version_uses_pyproject(self):
        from pmo import __version__
        expected=tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version']
        self.assertEqual(__version__,expected)
        self.assertNotIn('"version": "0.1.0"',(ROOT/'core/views.py').read_text())
