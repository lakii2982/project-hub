"""Fail when committed candidate metadata does not match a fresh source bundle.

The release candidate stores its archive SHA-256 and package-internal manifest digest
under ``docs/candidates/<version>/``.  Those files are excluded from the source ZIP, so
editing the metadata cannot change the archive digest -- but editing packaged files
without rebuilding makes the committed digests stale.  This gate rebuilds nothing; it
compares the metadata against the bundle that ``scripts/package.py`` just produced
(CI runs it after the package step) and exits non-zero on any mismatch.
"""
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def check(root):
    root = Path(root)
    version = __import__("tomllib").loads(
        (root / "pyproject.toml").read_text())["project"]["version"]
    candidate_dir = root / "docs" / "candidates" / version
    candidate_file = candidate_dir / "candidate.json"
    if not candidate_file.exists():
        return {"ok": True, "version": version,
                "skipped": "no docs/candidates/<version>/candidate.json"}
    candidate = json.loads(candidate_file.read_text())
    errors = []
    if candidate.get("version") != version:
        errors.append({"path": "candidate.json", "reason": "version mismatch"})
    archive = root / "releases" / f"project-hub-{version}-source.zip"
    if not archive.exists():
        return {"ok": False, "version": version,
                "errors": [{"path": str(archive), "reason": "archive missing; run scripts/package.py"}]}
    build = candidate.get("build", {})
    digest = _sha256_bytes(archive.read_bytes())
    if build.get("sha256") != digest:
        errors.append({"path": "candidate.json:build.sha256",
                       "reason": f"committed {build.get('sha256')} != rebuilt {digest}"})
    sidecar = candidate_dir / "source.zip.sha256"
    expected_sidecar = f"{digest}  {archive.name}\n"
    if not sidecar.exists():
        errors.append({"path": str(sidecar), "reason": "source.zip.sha256 missing"})
    elif sidecar.read_text() != expected_sidecar:
        errors.append({"path": "source.zip.sha256", "reason": "does not match rebuilt archive"})
    with ZipFile(archive) as bundle:
        names = bundle.namelist()
        manifest = bundle.read("project-hub/SHA256MANIFEST.json")
    files = len(names) - 1
    if build.get("files") != files:
        errors.append({"path": "candidate.json:build.files",
                       "reason": f"committed {build.get('files')} != rebuilt {files}"})
    manifest_digest = _sha256_bytes(manifest)
    if build.get("manifest_sha256") != manifest_digest:
        errors.append({"path": "candidate.json:build.manifest_sha256",
                       "reason": f"committed {build.get('manifest_sha256')} != rebuilt {manifest_digest}"})
    return {"ok": not errors, "version": version, "candidate": candidate.get("candidate"),
            "archive": str(archive.relative_to(root)), "sha256": digest, "files": files,
            "manifest_sha256": manifest_digest, "errors": errors}


if __name__ == "__main__":
    import sys
    try:
        result = check(Path.cwd())
    except (OSError, ValueError, KeyError) as error:
        result = {"ok": False, "errors": [{"reason": str(error)}]}
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["ok"] else 1)
