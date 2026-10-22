from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "publish-release.py"
SPEC = importlib.util.spec_from_file_location("publish_release", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
publish_release = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = publish_release
SPEC.loader.exec_module(publish_release)

TAG = "v0.2.3"
SHA = "a" * 40
OTHER_SHA = "b" * 40
TOKEN = "temporary-secret-token"


class FakeGitHub:
    def __init__(
        self,
        release: dict[str, object] | None,
        *,
        view_error: str = "",
        corrupt_download: bool = False,
    ) -> None:
        self.release = release
        self.view_error = view_error
        self.corrupt_download = corrupt_download
        self.calls: list[list[str]] = []
        self.remote_zip = b""
        self.remote_sidecar = b""

    def __call__(self, args: list[str]) -> SimpleNamespace:
        self.calls.append(list(args))
        command = args[:2]

        if command == ["release", "view"]:
            if self.view_error:
                return SimpleNamespace(returncode=1, stdout="", stderr=self.view_error)
            if self.release is None:
                return SimpleNamespace(
                    returncode=1,
                    stdout="",
                    stderr="release not found: HTTP 404",
                )
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(self.release),
                stderr="",
            )

        if command == ["release", "create"]:
            self.remote_zip = Path(args[3]).read_bytes()
            self.remote_sidecar = Path(args[4]).read_bytes()
            self.release = {
                "isDraft": True,
                "targetCommitish": SHA,
                "tagName": TAG,
            }
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        if command == ["release", "upload"]:
            self.remote_zip = Path(args[3]).read_bytes()
            self.remote_sidecar = Path(args[4]).read_bytes()
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        if command == ["release", "download"]:
            directory = Path(args[args.index("--dir") + 1])
            patterns = [
                args[index + 1]
                for index, argument in enumerate(args)
                if argument == "--pattern"
            ]
            for pattern in patterns:
                if pattern.endswith(".sha256"):
                    (directory / pattern).write_bytes(self.remote_sidecar)
                else:
                    data = (
                        b"corrupt remote archive"
                        if self.corrupt_download
                        else self.remote_zip
                    )
                    (directory / pattern).write_bytes(data)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        if command == ["release", "edit"]:
            if "--draft=false" not in args:
                return SimpleNamespace(
                    returncode=1,
                    stdout="",
                    stderr="--draft=false missing",
                )
            assert self.release is not None
            self.release["isDraft"] = False
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        return SimpleNamespace(returncode=99, stdout="", stderr=f"unexpected command: {args}")


class PublishReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="publish-release-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.assets_dir = self.root / "releases"
        self.assets_dir.mkdir()
        self.notes = self.root / "CHANGELOG.md"
        self.notes.write_text("# Changes\n", encoding="utf-8")

        self.zip_name = "project-hub-0.2.3-source.zip"
        self.zip_path = self.assets_dir / self.zip_name
        with ZipFile(self.zip_path, "w", ZIP_DEFLATED) as archive:
            archive.writestr("project-hub/README.md", "release contents\n")
        self.digest = hashlib.sha256(self.zip_path.read_bytes()).hexdigest()
        self.sidecar = self.assets_dir / f"{self.zip_name}.sha256"
        self.sidecar.write_text(f"{self.digest}  {self.zip_name}\n", encoding="utf-8")

    def run_cli(self, fake: FakeGitHub) -> tuple[int, dict[str, object], str]:
        output = io.StringIO()
        environment = {"GH_REPO": "owner/repository", "GH_TOKEN": TOKEN}
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(publish_release, "run_gh", fake),
            redirect_stdout(output),
        ):
            code = publish_release.main(
                [
                    "--tag",
                    TAG,
                    "--sha40hex",
                    SHA,
                    "--assets-dir",
                    str(self.assets_dir),
                    "--notes",
                    str(self.notes),
                ]
            )
        return code, json.loads(output.getvalue()), output.getvalue()

    def test_new_release_creates_draft_verifies_then_publishes_prerelease(self) -> None:
        fake = FakeGitHub(None)

        code, payload, _ = self.run_cli(fake)

        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["state"], "published")
        self.assertFalse(payload["idempotent"])
        self.assertEqual(
            [call[:2] for call in fake.calls],
            [
                ["release", "view"],
                ["release", "create"],
                ["release", "download"],
                ["release", "edit"],
            ],
        )
        create = fake.calls[1]
        self.assertIn("--draft", create)
        self.assertIn("--verify-tag", create)
        self.assertEqual(create[create.index("--target") + 1], SHA)
        self.assertEqual(create[-1], str(self.notes.resolve()))
        self.assertIn("--prerelease", fake.calls[3])
        self.assertFalse(fake.release["isDraft"])

    def test_already_public_same_target_is_verified_read_only_idempotency(self) -> None:
        fake = FakeGitHub(
            {"isDraft": False, "targetCommitish": SHA, "tagName": TAG}
        )
        fake.remote_zip = self.zip_path.read_bytes()
        fake.remote_sidecar = self.sidecar.read_bytes()

        code, payload, _ = self.run_cli(fake)

        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["state"], "already_published")
        self.assertTrue(payload["idempotent"])
        self.assertEqual(
            [call[:2] for call in fake.calls],
            [["release", "view"], ["release", "download"]],
        )

    def test_existing_release_with_different_target_is_rejected(self) -> None:
        fake = FakeGitHub(
            {"isDraft": False, "targetCommitish": OTHER_SHA, "tagName": TAG}
        )

        code, payload, _ = self.run_cli(fake)

        self.assertEqual(code, 1)
        self.assertFalse(payload["ok"])
        self.assertIn("target differs", payload["error"])
        self.assertEqual([call[:2] for call in fake.calls], [["release", "view"]])

    def test_remote_digest_mismatch_leaves_draft_unpublished(self) -> None:
        fake = FakeGitHub(
            {"isDraft": True, "targetCommitish": SHA, "tagName": TAG},
            corrupt_download=True,
        )
        fake.remote_zip = self.zip_path.read_bytes()
        fake.remote_sidecar = self.sidecar.read_bytes()

        code, payload, _ = self.run_cli(fake)

        self.assertEqual(code, 1)
        self.assertFalse(payload["ok"])
        self.assertIn("SHA256 differs", payload["error"])
        self.assertFalse(any(call[:2] == ["release", "edit"] for call in fake.calls))
        self.assertTrue(fake.release["isDraft"])

    def test_network_error_is_not_treated_as_missing_and_redacts_token(self) -> None:
        fake = FakeGitHub(
            None,
            view_error=f"dial tcp: connection refused token={TOKEN}",
        )

        code, payload, stdout = self.run_cli(fake)

        self.assertEqual(code, 1)
        self.assertFalse(payload["ok"])
        self.assertIn("[REDACTED]", payload["error"])
        self.assertNotIn(TOKEN, stdout)
        self.assertEqual([call[:2] for call in fake.calls], [["release", "view"]])
        self.assertFalse(any(call[:2] == ["release", "create"] for call in fake.calls))

    def test_draft_retry_uses_clobber_and_only_then_publishes(self) -> None:
        fake = FakeGitHub(
            {"isDraft": True, "targetCommitish": SHA, "tagName": TAG}
        )
        fake.remote_zip = self.zip_path.read_bytes()
        fake.remote_sidecar = self.sidecar.read_bytes()

        code, payload, _ = self.run_cli(fake)

        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["state"], "published")
        self.assertEqual(
            [call[:2] for call in fake.calls],
            [
                ["release", "view"],
                ["release", "upload"],
                ["release", "download"],
                ["release", "edit"],
            ],
        )
        self.assertIn("--clobber", fake.calls[1])
        self.assertIn("--prerelease", fake.calls[3])

    def test_local_checksum_mismatch_stops_before_any_gh_call(self) -> None:
        self.sidecar.write_text(f"{'0' * 64}  {self.zip_name}\n", encoding="utf-8")
        fake = FakeGitHub(None)

        code, payload, _ = self.run_cli(fake)

        self.assertEqual(code, 1)
        self.assertIn("does not match", payload["error"])
        self.assertEqual(fake.calls, [])

    def test_run_gh_uses_an_argument_array_without_a_shell(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        with patch.object(publish_release.subprocess, "run", return_value=completed) as run:
            actual = publish_release.run_gh(["--version"])

        self.assertIs(actual, completed)
        run.assert_called_once_with(
            ["gh", "--version"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=120,
        )

    def test_unexpected_public_asset_content_is_never_overwritten(self) -> None:
        fake = FakeGitHub(
            {"isDraft": False, "targetCommitish": SHA, "tagName": TAG}
        )
        fake.remote_zip = b"different public archive"
        fake.remote_sidecar = self.sidecar.read_bytes()

        code, payload, _ = self.run_cli(fake)

        self.assertEqual(code, 1)
        self.assertIn("differs", payload["error"])
        self.assertFalse(any(call[:2] == ["release", "upload"] for call in fake.calls))
        self.assertFalse(any(call[:2] == ["release", "edit"] for call in fake.calls))

    def test_unsafe_asset_name_is_rejected(self) -> None:
        for name in ("../source.zip", "/source.zip", "-source.zip", "source\nname.zip"):
            with self.subTest(name=name):
                with self.assertRaises(publish_release.ReleaseError):
                    publish_release.validate_asset_name(name)


if __name__ == "__main__":
    unittest.main()
