"""Small, deterministic fingerprints for reproducible parsing and source review."""

from __future__ import annotations

import hashlib
import json
from importlib.metadata import version
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fingerprint(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def runtime_identity() -> dict[str, Any]:
    package = Path(__file__).parent
    return {
        "code_sha256": fingerprint({p.name: sha256_file(p) for p in sorted(package.glob("*.py"))}),
        "dependencies": {name: version(name) for name in ("PyMuPDF", "PyYAML", "typer", "rich")},
    }
