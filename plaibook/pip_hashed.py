# -*- coding: utf-8 -*-
"""Locate checked-in hashed requirements and build pip --require-hashes argv."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

HASHED_DIR = Path(__file__).resolve().parent / "hashed"
_DIST_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]+\])?==([^\\\s]+)")


def hashed_requirements(filename: str) -> Path:
    path = HASHED_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"hashed requirements missing: {path}")
    return path


def pip_install_hashed_argv(python: str, filename: str, *, dry_run: bool = False) -> list[str]:
    argv = [python, "-m", "pip", "install", "--disable-pip-version-check"]
    if dry_run:
        argv.append("--dry-run")
    argv += ["--require-hashes", "-r", str(hashed_requirements(filename))]
    return argv


def pinned_versions(filename: str) -> dict[str, str]:
    """Map PEP 503 distribution names to the exact versions in ``filename``."""
    pins: dict[str, str] = {}
    for raw in hashed_requirements(filename).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("--"):
            continue
        match = _DIST_PIN.match(line)
        if not match:
            continue
        pins[_normalize_dist(match.group(1))] = match.group(2)
    return pins


def lock_digest(*filenames: str) -> str:
    """Stable identity of hashed requirement files (stamp / cache keys)."""
    digest = hashlib.sha256()
    for name in filenames:
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashed_requirements(name).read_bytes())
    return digest.hexdigest()


def _normalize_dist(name: str) -> str:
    return name.replace("_", "-").lower()
