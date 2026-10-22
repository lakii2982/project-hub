"""Publish a verified source archive to GitHub without overwriting public releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Sequence


TASK_ID = "PH-RELEASE-IDEMPOTENCY"
TAG_RE = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ASSET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SECRET_RE = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]+)\b"
)


class ReleaseError(RuntimeError):
    """A release operation that must stop before the next mutation."""


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ReleaseError(f"invalid arguments: {message}")


def run_gh(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run gh with an argument array; tests can replace this function."""
    command = ["gh", *(str(arg) for arg in args)]
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=120,
    )


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _redact(message: str) -> str:
    token = os.environ.get("GH_TOKEN", "")
    if token:
        message = message.replace(token, "[REDACTED]")
    message = SECRET_RE.sub("[REDACTED]", message)
    return " ".join(message.split())[:1200]


def _process_error(completed: subprocess.CompletedProcess[str]) -> str:
    detail = " ".join(
        part
        for part in (_text(completed.stderr).strip(), _text(completed.stdout).strip())
        if part
    )
    return _redact(detail) or f"exit status {completed.returncode}"


def _checked(args: Sequence[str], action: str) -> subprocess.CompletedProcess[str]:
    completed = run_gh(list(args))
    if completed.returncode != 0:
        raise ReleaseError(f"{action} failed: {_process_error(completed)}")
    return completed


def _release_not_found(completed: subprocess.CompletedProcess[str]) -> bool:
    if completed.returncode == 0:
        return False
    message = " ".join((_text(completed.stderr), _text(completed.stdout))).casefold()
    patterns = (
        r"\brelease\b.{0,120}\bnot found\b",
        r"\bnot found\b.{0,120}\brelease\b",
        r"\b404\b.{0,120}(?:\brelease\b|/releases/tags/)",
        r"(?:\brelease\b|/releases/tags/).{0,120}\b404\b",
    )
    return any(re.search(pattern, message) for pattern in patterns)


def _validate_tag(tag: str) -> tuple[str, bool]:
    match = TAG_RE.fullmatch(tag)
    if match is None:
        raise ReleaseError("--tag must be vMAJOR.MINOR.PATCH without leading zeroes")
    version = tag[1:]
    return version, version.startswith("0.")


def _validate_sha(sha: str) -> str:
    if SHA_RE.fullmatch(sha) is None:
        raise ReleaseError("--sha40hex must be exactly 40 hexadecimal characters")
    return sha.lower()


def validate_asset_name(name: str) -> str:
    """Reject separators, option-like names and other release-asset hazards."""
    if (
        not name
        or name in {".", ".."}
        or name.startswith("-")
        or "/" in name
        or "\\" in name
        or "\x00" in name
        or any(ord(character) < 32 for character in name)
        or ASSET_RE.fullmatch(name) is None
    ):
        raise ReleaseError(f"unsafe release asset name: {name!r}")
    return name


def _regular_file(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise ReleaseError(f"{label} must not be a symbolic link")
    if not path.is_file():
        raise ReleaseError(f"{label} is missing or not a regular file: {path}")
    return path.resolve(strict=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_sidecar(path: Path, expected_name: str) -> str:
    try:
        fields = path.read_text(encoding="utf-8").split()
    except (OSError, UnicodeError) as error:
        raise ReleaseError(f"cannot read checksum file: {error}") from error
    if len(fields) != 2:
        raise ReleaseError("checksum file must contain exactly a SHA256 and filename")
    digest, name = fields
    if validate_asset_name(name) != expected_name:
        raise ReleaseError("checksum filename does not match the source archive")
    if re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None:
        raise ReleaseError("checksum file does not contain a valid SHA256")
    return digest.lower()


def validate_local_assets(assets_dir: Path, tag: str) -> dict[str, Any]:
    version, _ = _validate_tag(tag)
    if assets_dir.is_symlink():
        raise ReleaseError("--assets-dir must not be a symbolic link")
    if not assets_dir.is_dir():
        raise ReleaseError(f"--assets-dir is missing or not a directory: {assets_dir}")
    assets_dir = assets_dir.resolve(strict=True)

    zip_name = validate_asset_name(f"project-hub-{version}-source.zip")
    sidecar_name = validate_asset_name(f"{zip_name}.sha256")
    zip_path = _regular_file(assets_dir / zip_name, "source archive")
    sidecar_path = _regular_file(assets_dir / sidecar_name, "checksum file")

    actual_digest = _sha256(zip_path)
    declared_digest = _read_sidecar(sidecar_path, zip_name)
    if actual_digest != declared_digest:
        raise ReleaseError("local source archive SHA256 does not match its checksum file")

    return {
        "zip_path": zip_path,
        "sidecar_path": sidecar_path,
        "zip_name": zip_name,
        "sidecar_name": sidecar_name,
        "digest": actual_digest,
        "sidecar_bytes": sidecar_path.read_bytes(),
    }


def validate_notes(notes: Path) -> Path:
    return _regular_file(notes, "--notes file")


def validate_environment() -> str:
    repo = os.environ.get("GH_REPO", "")
    if REPO_RE.fullmatch(repo) is None:
        raise ReleaseError("GH_REPO must identify one GitHub repository as owner/name")
    if not os.environ.get("GH_TOKEN"):
        raise ReleaseError("GH_TOKEN is required")
    return repo


def view_release(tag: str) -> dict[str, Any] | None:
    completed = run_gh(
        ["release", "view", tag, "--json", "isDraft,targetCommitish,tagName"]
    )
    if completed.returncode != 0:
        if _release_not_found(completed):
            return None
        raise ReleaseError(f"release view failed: {_process_error(completed)}")

    try:
        release = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as error:
        raise ReleaseError("release view returned invalid JSON") from error
    if not isinstance(release, dict):
        raise ReleaseError("release view returned a non-object JSON value")
    if not isinstance(release.get("isDraft"), bool):
        raise ReleaseError("release view is missing boolean isDraft")
    if release.get("tagName") != tag:
        raise ReleaseError("release view tag does not match --tag")
    if not isinstance(release.get("targetCommitish"), str):
        raise ReleaseError("release view is missing targetCommitish")
    return release


def _same_target(release: dict[str, Any], sha: str) -> bool:
    target = release.get("targetCommitish")
    return isinstance(target, str) and target.casefold() == sha.casefold()


def _download_and_verify(tag: str, local: dict[str, Any]) -> None:
    with tempfile.TemporaryDirectory(prefix=f"publish-release-{tag}-") as directory:
        remote_dir = Path(directory)
        _checked(
            [
                "release",
                "download",
                tag,
                "--dir",
                str(remote_dir),
                "--pattern",
                local["zip_name"],
                "--pattern",
                local["sidecar_name"],
                "--clobber",
            ],
            "release download",
        )
        remote_zip = _regular_file(
            remote_dir / local["zip_name"], "downloaded source archive"
        )
        remote_sidecar = _regular_file(
            remote_dir / local["sidecar_name"], "downloaded checksum file"
        )

        remote_digest = _sha256(remote_zip)
        if remote_digest != local["digest"]:
            raise ReleaseError(
                "downloaded source archive SHA256 differs from the local archive"
            )
        remote_declared = _read_sidecar(remote_sidecar, local["zip_name"])
        if remote_declared != local["digest"]:
            raise ReleaseError("downloaded checksum file differs from the local archive SHA256")
        if remote_sidecar.read_bytes() != local["sidecar_bytes"]:
            raise ReleaseError("downloaded checksum file differs from the local checksum file")


def publish(tag: str, sha: str, assets_dir: Path, notes: Path) -> dict[str, Any]:
    version, prerelease = _validate_tag(tag)
    sha = _validate_sha(sha)
    validate_environment()
    local = validate_local_assets(assets_dir, tag)
    notes_path = validate_notes(notes)

    release = view_release(tag)
    newly_created = release is None
    repaired_draft = False

    if release is None:
        _checked(
            [
                "release",
                "create",
                tag,
                str(local["zip_path"]),
                str(local["sidecar_path"]),
                "--draft",
                "--target",
                sha,
                "--verify-tag",
                "--notes-file",
                str(notes_path),
            ],
            "release create",
        )
    elif not _same_target(release, sha):
        raise ReleaseError("existing release target differs from --sha40hex")
    elif release["isDraft"]:
        _checked(
            [
                "release",
                "upload",
                tag,
                str(local["zip_path"]),
                str(local["sidecar_path"]),
                "--clobber",
            ],
            "release upload",
        )
        repaired_draft = True

    _download_and_verify(tag, local)

    if newly_created or repaired_draft:
        edit_args = ["release", "edit", tag, "--draft=false"]
        if prerelease:
            edit_args.append("--prerelease")
        _checked(edit_args, "release edit")
        state = "published"
        idempotent = False
    else:
        state = "already_published"
        idempotent = True

    return {
        "task_id": TASK_ID,
        "ok": True,
        "tag": tag,
        "version": version,
        "sha": sha,
        "state": state,
        "idempotent": idempotent,
        "prerelease": prerelease,
        "assets_verified": [local["zip_name"], local["sidecar_name"]],
        "sha256": local["digest"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--sha40hex", required=True)
    parser.add_argument("--assets-dir", type=Path, default=Path("releases"))
    parser.add_argument("--notes", type=Path, default=Path("CHANGELOG.md"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args: argparse.Namespace | None = None
    try:
        args = _parser().parse_args(argv)
        result = publish(args.tag, args.sha40hex, args.assets_dir, args.notes)
    except (ReleaseError, OSError, ValueError, subprocess.SubprocessError) as error:
        payload: dict[str, Any] = {
            "task_id": TASK_ID,
            "ok": False,
            "error": _redact(str(error)),
        }
        if args is not None:
            payload["tag"] = args.tag
            payload["sha"] = args.sha40hex
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 1

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
