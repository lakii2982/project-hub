"""Verify the source ZIP, manifest and exclusion policy without extracting runtime data."""
import hashlib
import json
import stat
import sys
from pathlib import Path, PurePosixPath
from zipfile import ZipFile


def verify(path):
    blocked = {"runtime", "backups", "releases", ".git", ".venv", ".agent-venv", "__pycache__", "node_modules", ".cache", "staticfiles"}
    with ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or archive.testzip() is not None:
            raise ValueError("invalid ZIP entries")
        files = {}
        for info in archive.infolist():
            name = PurePosixPath(info.filename)
            if name.is_absolute() or ".." in name.parts or name.parts[0] != "project-hub" or len(name.parts) < 2:
                raise ValueError("unsafe source path")
            relative = PurePosixPath(*name.parts[1:])
            if set(relative.parts) & blocked or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError("excluded source path")
            if relative.name.startswith(".env") and str(relative) != ".env.example":
                raise ValueError("environment file in source")
            if relative.suffix in {".sqlite3", ".pyc", ".pyo", ".pem", ".key", ".p12"}:
                raise ValueError("runtime or credential artifact in source")
            files[str(relative)] = archive.read(info)
        manifest = json.loads(files.pop("SHA256MANIFEST.json"))
        actual = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
        if manifest != actual:
            raise ValueError("source manifest mismatch")
        required = {"LICENSE", "NOTICE", "CONTRIBUTING.md", "project/manifest.json", "project/tasks.json", "README.md", "AGENTS.md", "manage.py", "pyproject.toml", "requirements.txt", "compose.yaml", "core/models.py", "integrations/mcp/server.py"}
        if not required <= files.keys():
            raise ValueError("source entry point missing")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    checksum = path.with_suffix(".zip.sha256").read_text().split()
    if checksum != [digest, path.name]:
        raise ValueError("archive SHA256 mismatch")
    return {"ok": True, "files": len(files), "sha256": digest, "runtime_excluded": True}


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise ValueError("usage: verify-source-package.py <source.zip>")
        print(json.dumps(verify(Path(sys.argv[1]))))
    except (OSError, ValueError, KeyError) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        raise SystemExit(1)
