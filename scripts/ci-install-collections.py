#!/usr/bin/env python3
"""Install collections-requirements.yml for CI without requiring Galaxy.

``ansible-galaxy collection install -r`` resolves unpinned Galaxy names
against galaxy.ansible.com before it clones anything. A 403 HTML body
becomes an unhandled parse error and ansible-galaxy exits 250 in a few
seconds — that is the failure mode on GitHub-hosted runners.

Try ``-r`` first (when Galaxy works it also pulls declared deps). On
failure, clone each git source and known GitHub mirrors with
``--no-deps`` so the job never needs the Galaxy API.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

from plaibook.collections import (
    GALAXY_GITHUB_MIRRORS,
    GALAXY_TIMEOUT_SECONDS,
    collection_is_installed,
    requirement_collection_keys,
)

REQUIREMENTS = Path("collections-requirements.yml")


def _dest() -> Path:
    raw = os.environ.get("PLAIBOOK_COLLECTIONS_DEST") or os.environ.get("ANSIBLE_COLLECTIONS_PATH", "")
    if not raw:
        raise SystemExit("PLAIBOOK_COLLECTIONS_DEST or ANSIBLE_COLLECTIONS_PATH is required")
    path = Path(raw.split(os.pathsep)[0])
    path.mkdir(parents=True, exist_ok=True)
    return path


def _requirements() -> list[dict]:
    data = yaml.safe_load(REQUIREMENTS.read_bytes()) or {}
    cols = data.get("collections") or []
    if not isinstance(cols, list):
        raise SystemExit(f"{REQUIREMENTS} collections: is not a list")
    return cols


def _installed_count(dest: Path) -> int:
    root = dest / "ansible_collections"
    if not root.is_dir():
        return 0
    found: set[tuple[str, str]] = set()
    for ns in root.iterdir():
        if not ns.is_dir() or ns.name.startswith("."):
            continue
        for name in ns.iterdir():
            if not name.is_dir():
                continue
            if (name / "MANIFEST.json").is_file() or (name / "galaxy.yml").is_file():
                found.add((ns.name, name.name))
    return len(found)


def _required_keys() -> list[tuple[str, str]]:
    keys = list(requirement_collection_keys(REQUIREMENTS))
    seen = set(keys)
    for fqn in GALAXY_GITHUB_MIRRORS:
        ns, name = fqn.split(".", 1)
        key = (ns, name)
        if key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def _all_required_installed(dest: Path, required: list[tuple[str, str]]) -> bool:
    return all(collection_is_installed(dest, ns, name) for ns, name in required)


def _galaxy_bin() -> str:
    found = shutil.which("ansible-galaxy")
    if not found:
        raise SystemExit("ansible-galaxy not on PATH")
    return found


def _git_token() -> str | None:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return None
    token = os.environ.get("GITHUB_TOKEN") or ""
    return token or None


def _git(args: list[str], *, cwd: Path | None = None, token: str | None) -> None:
    cmd = ["git", "-c", "advice.detachedHead=false"]
    if token:
        cmd += [
            "-c",
            f"url.https://x-access-token:{token}@github.com/.insteadOf=https://github.com/",
        ]
    cmd += args
    subprocess.run(cmd, cwd=cwd, check=True, timeout=GALAXY_TIMEOUT_SECONDS)


def _clone_at_ref(url: str, ref: str, dest: Path, token: str | None) -> None:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        try:
            _git(["init", "-b", "main"], cwd=dest, token=token)
            _git(["remote", "add", "origin", url], cwd=dest, token=token)
            _git(["fetch", "--depth", "1", "origin", ref], cwd=dest, token=token)
            _git(["checkout", "FETCH_HEAD"], cwd=dest, token=token)
            return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            last_error = exc
            print(f"git fetch {url}@{ref} attempt {attempt} failed", flush=True)
            time.sleep(attempt * 4)
    raise SystemExit(f"git fetch failed for {url}@{ref}: {last_error}")


def _install_from_dir(galaxy: str, source: Path, dest: Path) -> None:
    try:
        completed = subprocess.run(
            [
                galaxy,
                "collection",
                "install",
                str(source),
                "-p",
                str(dest),
                "--force",
                "--no-deps",
            ],
            check=False,
            timeout=GALAXY_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise SystemExit(f"ansible-galaxy install {source} timed out after {GALAXY_TIMEOUT_SECONDS}s") from exc
    if completed.returncode != 0:
        raise SystemExit(f"ansible-galaxy install {source} failed (exit {completed.returncode})")


def try_requirements_file(galaxy: str, dest: Path) -> bool:
    """Return True when ``-r`` succeeded. Galaxy 403s usually exit 250."""
    if os.environ.get("PLAIBOOK_CI_SKIP_GALAXY") == "1":
        print("Skipping ansible-galaxy -r (PLAIBOOK_CI_SKIP_GALAXY=1)", flush=True)
        return False
    try:
        completed = subprocess.run(
            [
                galaxy,
                "collection",
                "install",
                "-r",
                str(REQUIREMENTS),
                "-p",
                str(dest),
                "--force",
            ],
            check=False,
            timeout=GALAXY_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        print(
            f"ansible-galaxy -r timed out after {GALAXY_TIMEOUT_SECONDS}s",
            flush=True,
        )
        return False
    if completed.returncode == 0:
        return True
    print(
        f"ansible-galaxy -r failed (exit {completed.returncode})",
        flush=True,
    )
    return False


def install_git_sources(galaxy: str, dest: Path, cols: list[dict], token: str | None) -> None:
    for col in cols:
        name = col.get("name")
        if not isinstance(name, str):
            continue
        is_git = col.get("type") == "git" or name.startswith(("https://", "git+", "git@"))
        if not is_git:
            continue
        url = name.removeprefix("git+")
        ref = str(col.get("version") or "HEAD")
        print(f"GitHub fallback: {url}@{ref}", flush=True)
        with tempfile.TemporaryDirectory(prefix="plaibook-coll-") as tmp:
            checkout = Path(tmp) / "collection"
            _clone_at_ref(url, ref, checkout, token)
            _install_from_dir(galaxy, checkout, dest)


def install_galaxy_mirrors(galaxy: str, dest: Path, token: str | None) -> None:
    root = dest / "ansible_collections"
    for fqn, (url, ref) in GALAXY_GITHUB_MIRRORS.items():
        ns, name = fqn.split(".", 1)
        marker = root / ns / name
        if (marker / "MANIFEST.json").is_file() or (marker / "galaxy.yml").is_file():
            print(f"GitHub fallback: {fqn} already present", flush=True)
            continue
        print(f"GitHub fallback: {fqn} <- {url}@{ref}", flush=True)
        with tempfile.TemporaryDirectory(prefix="plaibook-coll-") as tmp:
            checkout = Path(tmp) / "collection"
            _clone_at_ref(url, ref, checkout, token)
            _install_from_dir(galaxy, checkout, dest)


def main() -> int:
    os.environ.setdefault("GIT_TERMINAL_PROMPT", "0")
    dest = _dest()
    cols = _requirements()
    required = _required_keys()
    if _all_required_installed(dest, required):
        print(
            f"Using cached collections ({_installed_count(dest)} installed, {len(required)} required)",
            flush=True,
        )
        return 0

    galaxy = _galaxy_bin()
    token = _git_token()
    if try_requirements_file(galaxy, dest) and _all_required_installed(dest, required):
        print(f"Installed {_installed_count(dest)} collections via -r", flush=True)
        return 0

    print(
        "Galaxy resolve failed or incomplete; installing git sources and GitHub mirrors with --no-deps",
        flush=True,
    )
    install_git_sources(galaxy, dest, cols, token)
    install_galaxy_mirrors(galaxy, dest, token)
    if not _all_required_installed(dest, required):
        missing = [f"{ns}.{name}" for ns, name in required if not collection_is_installed(dest, ns, name)]
        print(
            f"ERROR: missing required collections: {', '.join(missing)}",
            file=sys.stderr,
        )
        return 1
    print(f"Installed {_installed_count(dest)} collections via GitHub fallback", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
