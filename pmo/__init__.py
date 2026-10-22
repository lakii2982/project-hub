"""Project version has one source: pyproject.toml."""
from pathlib import Path
import tomllib

__version__ = tomllib.loads((Path(__file__).resolve().parent.parent / "pyproject.toml").read_text())["project"]["version"]
