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


class LightDeliveryDocsTests(unittest.TestCase):
    """The four living documents and the light protocol are the only process entry."""

    LIVING = ['project/QUEUE.md', 'project/STATUS.md', 'project/ACCEPTANCE.md',
              'project/DECISIONS.md', 'docs/engineering/light-delivery.md']

    def read(self, relative):
        return (ROOT / relative).read_text(encoding='utf-8')

    def test_living_documents_exist_and_are_required(self):
        for relative in self.LIVING:
            with self.subTest(path=relative):
                self.assertTrue((ROOT / relative).is_file())
        manifest = json.loads(self.read('project/manifest.json'))
        self.assertEqual(manifest['active_handoff'], 'project/STATUS.md')
        for relative in self.LIVING:
            self.assertIn(relative, manifest['required_docs'])

    def test_agents_points_at_the_protocol_and_keeps_the_invariants(self):
        agents = self.read('AGENTS.md')
        self.assertIn('docs/engineering/light-delivery.md', agents)
        self.assertIn('project/QUEUE.md', agents)
        self.assertIn('project/STATUS.md', agents)
        self.assertIn('project/ACCEPTANCE.md', agents)
        self.assertIn('project/DECISIONS.md', agents)
        for invariant in ('整数分', 'ROUND_HALF_UP', '禁止 float', '只追加',
                          'select_for_update', '真实客户'):
            self.assertIn(invariant, agents)
        self.assertIn('gh pr merge <n> --auto --squash --match-head-commit <sha>', agents)
        self.assertIn('## 验收与交付', agents)
        self.assertIn('manage.py test tests --verbosity 1', agents)

    def test_protocol_states_merge_and_review_rules(self):
        protocol = self.read('docs/engineering/light-delivery.md')
        self.assertIn('gh pr merge <n> --auto --squash --match-head-commit <sha>', protocol)
        self.assertIn('没有人工合并步骤', protocol)
        self.assertIn('9 项必需检查', protocol)
        self.assertIn('独立复核', protocol)
        self.assertIn('被复核的提交就是被合并的提交', protocol)
        self.assertIn('ROUND_HALF_UP', protocol)
        for heading in ('队列', '票据模板', '合并条件', '验收节奏', '停止规则', '不可让步的底线'):
            self.assertIn(heading, protocol)

    def test_history_is_archived_not_deleted(self):
        archive = ROOT / 'docs/archive/v1.0-planning'
        self.assertTrue((archive / 'README.md').is_file())
        for name in ('batch-c-acceptance.md', 'batch-d-acceptance.md',
                     'batch-d-execution-orchestration.md', 'closure-reconciliation.md',
                     'release-evidence-backfill.md', 'requirement-trace.json',
                     'implementation-plan.json', 'schedule.json', 'work-packages.json',
                     'work-package-index.md', 'delivery-status-archive.md'):
            with self.subTest(name=name):
                self.assertTrue((archive / name).is_file(), name)
                self.assertFalse((ROOT / 'docs/v1.0-development' / name).exists(), name)
        self.assertFalse((ROOT / 'docs/delivery-status-archive.md').exists())
        status = self.read('docs/delivery-status.md')
        self.assertIn('project/STATUS.md', status)
        self.assertIn('docs/archive/v1.0-planning/', status)
        self.assertIn('docs/engineering/light-delivery.md', self.read('ROADMAP.md')[:4000])

    def test_queue_contract_holds_the_first_wave_outcomes(self):
        from scripts import projectctl
        parsed = projectctl.parse_queue(ROOT, json.loads(self.read('project/tasks.json'))['tasks'])
        self.assertEqual(parsed['issues'], [])
        # 2026-11-03 范围重定义：QUEUE 重排为小微版 14 条（Q-31–Q-44）；专业版 30 条归档只读。
        self.assertGreaterEqual(parsed['item_count'], 10)
        self.assertLessEqual(parsed['item_count'], 20)
        first_wave = [item for item in parsed['items'] if item['wave'] == 'W1']
        self.assertGreaterEqual(len(first_wave), 4)
        ids = {item['id'] for item in parsed['items']}
        self.assertTrue({'Q-31', 'Q-32', 'Q-33', 'Q-34', 'Q-35'} <= ids)
        self.assertNotIn('Q-01', ids)
        self.assertTrue((ROOT / 'docs/archive/v1.0-planning/queue-professional-2026-11-03.md').is_file())
        for item in first_wave:
            self.assertEqual(len(item['checks']) >= 3, True, item['id'])
            self.assertTrue(item['demo'], item['id'])
            self.assertTrue(item['legacy'], item['id'])
            self.assertTrue(item['requirements'], item['id'])
