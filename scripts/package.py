"""Produce a reproducible source bundle without runtime data, account passwords or credentials."""
import hashlib
import json
import tomllib
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

root = Path(__file__).resolve().parent.parent
output = root / "releases"
output.mkdir(exist_ok=True)
allowed_roots = {"core", "pmo", "pmo_plugins", "tests", "templates", "static", "scripts", "docs", "integrations", ".github", "project", ".cursor"}
allowed_files = {"manage.py", "requirements.txt", "requirements.in", "pyproject.toml", "README.md", "AGENTS.md", "CLAUDE.md", "GEMINI.md", "CHANGELOG.md", "LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "CONTRIBUTING.md", "SECURITY.md", "ROADMAP.md", "START_HERE.md", ".gitleaks.toml", "compose.yaml", "Dockerfile", "Caddyfile", ".env.example", ".dockerignore", ".gitignore"}
excluded_parts = {"__pycache__", ".git", ".venv", ".agent-venv", ".cache", "node_modules", "runtime", "staticfiles", "backups", "releases"}
files = []
for path in sorted(root.rglob("*")):
    rel = path.relative_to(root)
    if not path.is_file() or path.is_symlink(): continue
    if rel.parts[0] not in allowed_roots and str(rel) not in allowed_files: continue
    if set(rel.parts) & excluded_parts or path.suffix in {".pyc", ".pyo", ".sqlite3", ".pem", ".key", ".p12"}: continue
    if path.name.startswith(".env") and str(rel) != ".env.example": continue
    files.append((path, rel))
contents = {rel.as_posix(): path.read_bytes() for path, rel in files}
manifest = {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}
version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
bundle = output / f"project-hub-{version}-source.zip"

def add_file(archive, name, data):
    info = ZipInfo("project-hub/" + name, date_time=(1980, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.external_attr = (0o100755 if data.startswith(b"#!/") else 0o100644) << 16
    info.compress_type = ZIP_DEFLATED
    archive.writestr(info, data)

with ZipFile(bundle, "w", ZIP_DEFLATED) as archive:
    for name, data in contents.items(): add_file(archive, name, data)
    add_file(archive, "SHA256MANIFEST.json", json.dumps(manifest, indent=2, ensure_ascii=False).encode())
sha = hashlib.sha256(bundle.read_bytes()).hexdigest()
(bundle.with_suffix(".zip.sha256")).write_text(sha + "  " + bundle.name + "\n")
print(json.dumps({"archive": str(bundle), "files": len(files), "sha256": sha}, ensure_ascii=False))
