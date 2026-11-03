"""Q-44 digest gate: committed candidate metadata must match a rebuilt source bundle."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_candidate_metadata", ROOT / "scripts/check_candidate_metadata.py")
assert SPEC is not None and SPEC.loader is not None
gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gate
SPEC.loader.exec_module(gate)


class CandidateMetadataTests(unittest.TestCase):
    def fixture(self, directory):
        root = Path(directory)
        (root / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n')
        candidate_dir = root / "docs" / "candidates" / "0.2.0"
        candidate_dir.mkdir(parents=True)
        (root / "releases").mkdir()
        archive = root / "releases" / "project-hub-0.2.0-source.zip"
        manifest = b'{"a.py": "digest"}\n'
        with ZipFile(archive, "w", ZIP_DEFLATED) as bundle:
            bundle.writestr("project-hub/SHA256MANIFEST.json", manifest)
            bundle.writestr("project-hub/a.py", b"print('a')\n")
        import hashlib
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        manifest_digest = hashlib.sha256(manifest).hexdigest()
        (candidate_dir / "source.zip.sha256").write_text(f"{digest}  {archive.name}\n")
        (candidate_dir / "candidate.json").write_text(json.dumps({
            "candidate": "0.2.0-rc.1", "version": "0.2.0",
            "build": {"sha256": digest, "files": 1, "manifest_sha256": manifest_digest},
        }))
        return root, candidate_dir, digest, manifest_digest

    def test_matching_metadata_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root, _, digest, manifest_digest = self.fixture(directory)
            result = gate.check(root)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["sha256"], digest)
            self.assertEqual(result["manifest_sha256"], manifest_digest)
            self.assertEqual(result["files"], 1)

    def test_stale_archive_digest_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root, candidate_dir, _, _ = self.fixture(directory)
            candidate = json.loads((candidate_dir / "candidate.json").read_text())
            candidate["build"]["sha256"] = "0" * 64
            (candidate_dir / "candidate.json").write_text(json.dumps(candidate))
            result = gate.check(root)
            self.assertFalse(result["ok"])
            self.assertIn("candidate.json:build.sha256", {e["path"] for e in result["errors"]})

    def test_stale_manifest_digest_and_sidecar_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root, candidate_dir, digest, _ = self.fixture(directory)
            candidate = json.loads((candidate_dir / "candidate.json").read_text())
            candidate["build"]["manifest_sha256"] = "1" * 64
            (candidate_dir / "candidate.json").write_text(json.dumps(candidate))
            (candidate_dir / "source.zip.sha256").write_text("2" * 64 + "  project-hub-0.2.0-source.zip\n")
            result = gate.check(root)
            self.assertFalse(result["ok"])
            reasons = {e["path"] for e in result["errors"]}
            self.assertIn("candidate.json:build.manifest_sha256", reasons)
            self.assertIn("source.zip.sha256", reasons)
            self.assertEqual(result["sha256"], digest)

    def test_missing_candidate_metadata_is_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n')
            result = gate.check(root)
            self.assertTrue(result["ok"])
            self.assertIn("skipped", result)


if __name__ == "__main__":
    unittest.main()
