#!/usr/bin/env python3
"""Install collections-requirements.yml for CI without requiring Galaxy.

Clone each git source and GitHub mirrors with ``ansible-galaxy collection
build`` then ``install --no-deps``. Same GitHub-first path as
``plaibook.collections.ensure_collections`` — never ``ansible-galaxy -r``.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from plaibook.collections import (
    GALAXY_GITHUB_MIRRORS,
    GALAXY_TIMEOUT_SECONDS,
    CollectionInstallError,
    _load_requirement_rows,
    _runtime_required_keys,
    collection_is_installed,
    redact_git_userinfo,
    require_commit_sha,
    requirement_collection_keys,
)
from plaibook.collections import (
    _install_from_dir as _runtime_install_from_dir,
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
    try:
        return _load_requirement_rows(REQUIREMENTS)
    except CollectionInstallError as exc:
        raise SystemExit(str(exc)) from exc


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
    return _runtime_required_keys(requirement_collection_keys(REQUIREMENTS))


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
        # actions/checkout wire format: Basic, not Bearer. A Bearer header
        # makes GitHub prompt for a username and public clones fail closed
        # on Actions. Keep the token out of the clone URL (gitleaks).
        basic = base64.b64encode(b"x-access-token:" + token.encode("ascii")).decode("ascii")
        cmd += ["-c", f"http.https://github.com/.extraHeader=AUTHORIZATION: basic {basic}"]
    cmd += args
    subprocess.run(cmd, cwd=cwd, check=True, timeout=GALAXY_TIMEOUT_SECONDS)


def _clone_at_ref(url: str, ref: str, dest: Path, token: str | None) -> None:
    try:
        sha = require_commit_sha(url, ref)
    except CollectionInstallError as exc:
        raise SystemExit(str(exc)) from exc
    last_error: Exception | None = None
    for attempt in range(1, 4):
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        try:
            _git(["init", "-b", "main"], cwd=dest, token=token)
            _git(["remote", "add", "origin", url], cwd=dest, token=token)
            _git(["fetch", "--depth", "1", "origin", sha], cwd=dest, token=token)
            _git(["checkout", "FETCH_HEAD"], cwd=dest, token=token)
            got = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=dest,
                check=True,
                timeout=GALAXY_TIMEOUT_SECONDS,
                capture_output=True,
                text=True,
            )
            checked = got.stdout.strip().lower()
            if checked != sha:
                safe_url = redact_git_userinfo(url)
                raise SystemExit(f"git checkout of {safe_url} resolved to {checked}, not pinned {sha}")
            return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            last_error = exc
            safe_url = redact_git_userinfo(url)
            print(f"git fetch {safe_url}@{sha} attempt {attempt} failed", flush=True)
            time.sleep(attempt * 4)
    safe_url = redact_git_userinfo(url)
    detail = redact_git_userinfo(str(last_error))
    raise SystemExit(f"git fetch failed for {safe_url}@{sha}: {detail}")


def _install_from_dir(galaxy: str, source: Path, dest: Path) -> None:
    try:
        _runtime_install_from_dir(galaxy, source, dest, os.environ.copy())
    except CollectionInstallError as exc:
        raise SystemExit(str(exc)) from exc


def install_git_sources(galaxy: str, dest: Path, cols: list[dict], token: str | None) -> None:
    for col in cols:
        name = col.get("name")
        if not isinstance(name, str) or not name.strip():
            raise SystemExit("collection requirement is missing a non-empty string name")
        name = name.strip()
        is_git = col.get("type") == "git" or name.startswith(("https://", "http://", "git+", "git@", "file://"))
        if not is_git:
            continue
        url = name.removeprefix("git+")
        ref = require_commit_sha(url, col.get("version"))
        print(f"GitHub: {url}@{ref}", flush=True)
        with tempfile.TemporaryDirectory(prefix="plaibook-coll-") as tmp:
            checkout = Path(tmp) / "collection"
            _clone_at_ref(url, ref, checkout, token)
            _install_from_dir(galaxy, checkout, dest)


def install_galaxy_mirrors(
    galaxy: str,
    dest: Path,
    token: str | None,
    *,
    needed: list[tuple[str, str]] | None = None,
) -> None:
    want = set(needed) if needed is not None else None
    root = dest / "ansible_collections"
    for fqn, (url, ref) in GALAXY_GITHUB_MIRRORS.items():
        ns, name = fqn.split(".", 1)
        if want is not None and (ns, name) not in want:
            continue
        marker = root / ns / name
        if (marker / "MANIFEST.json").is_file() or (marker / "galaxy.yml").is_file():
            print(f"GitHub: {fqn} already present", flush=True)
            continue
        print(f"GitHub: {fqn} <- {url}@{ref}", flush=True)
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
    print("Installing collections from GitHub (no galaxy.ansible.com)", flush=True)
    install_git_sources(galaxy, dest, cols, token)
    install_galaxy_mirrors(galaxy, dest, token, needed=required)
    if not _all_required_installed(dest, required):
        missing = [f"{ns}.{name}" for ns, name in required if not collection_is_installed(dest, ns, name)]
        print(
            f"ERROR: missing required collections: {', '.join(missing)}",
            file=sys.stderr,
        )
        return 1
    print(f"Installed {_installed_count(dest)} collections from GitHub", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
