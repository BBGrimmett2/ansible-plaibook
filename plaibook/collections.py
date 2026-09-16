# -*- coding: utf-8 -*-
"""Install Galaxy collections into a plaibook-owned cache, never ~/.ansible."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import TextIO
from urllib.parse import urlparse

import yaml

from plaibook.playbook import (
    CACHE_DIRNAME,
    ansible_tool_bin,
    last_run_dir,
)

REQUIREMENTS_NAME = "collections-requirements.yml"
COLLECTIONS_DIRNAME = "collections"
STAMP_NAME = ".requirements.sha256"
ENV_COLLECTIONS = "ANSIBLE_COLLECTIONS_PATH"
ENV_COLLECTIONS_LEGACY = "ANSIBLE_COLLECTIONS_PATHS"
GALAXY_TIMEOUT_SECONDS = 600


class CollectionInstallError(RuntimeError):
    """ansible-galaxy could not install collections-requirements.yml."""


def ansible_galaxy_bin() -> str:
    return ansible_tool_bin("ansible-galaxy")


def collections_dir(home: Path | None = None) -> Path:
    return last_run_dir(home) / COLLECTIONS_DIRNAME


def merge_collections_path(env: dict[str, str], *, home: Path | None = None) -> None:
    """Put the isolated cache first so a leftover ~/.ansible tree never wins.

    ansible-core 2.19+ refuses to start when ANSIBLE_COLLECTIONS_PATHS is set.
    Read the legacy name if present, then drop it.
    """
    isolated = str(collections_dir(home))
    raw = env.get(ENV_COLLECTIONS) or env.get(ENV_COLLECTIONS_LEGACY) or ""
    parts = [p for p in raw.split(os.pathsep) if p and p != isolated]
    env[ENV_COLLECTIONS] = os.pathsep.join([isolated, *parts])
    env.pop(ENV_COLLECTIONS_LEGACY, None)


def _key_from_fqcn(name: str) -> tuple[str, str] | None:
    if "://" in name or "/" in name or name.endswith(".git"):
        return None
    ns, sep, rest = name.partition(".")
    if sep and ns and rest:
        return (ns, rest)
    return None


def _key_from_git_url(url: str) -> tuple[str, str] | None:
    """Map a git collection URL to the ns/name dir ansible-galaxy installs."""
    raw = url.removeprefix("git+")
    if raw.startswith("git@"):
        _, _, rest = raw.partition(":")
        path = rest
    else:
        path = urlparse(raw).path.lstrip("/")
    path = path.removesuffix(".git").rstrip("/")
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[-2], parts[-1]
    if repo.startswith("ansible-") and repo != "ansible-":
        return (owner, repo[len("ansible-") :])
    dotted = _key_from_fqcn(repo)
    if dotted:
        return dotted
    return (owner, repo)


def collection_key_from_requirement(col: object) -> tuple[str, str] | None:
    """Return (namespace, name) for one collections-requirements.yml entry."""
    if not isinstance(col, dict):
        return None
    name = col.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    if col.get("type") == "git" or name.startswith(("https://", "http://", "git+", "git@")):
        return _key_from_git_url(name)
    return _key_from_fqcn(name)


def requirement_collection_keys(requirements: Path) -> list[tuple[str, str]]:
    """Named collections the stamp must contain — not a raw directory count."""
    data = yaml.safe_load(requirements.read_bytes()) or {}
    cols = data.get("collections") or []
    if not isinstance(cols, list):
        return []
    keys: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for col in cols:
        key = collection_key_from_requirement(col)
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def collection_is_installed(dest: Path, ns: str, name: str) -> bool:
    coll = dest / "ansible_collections" / ns / name
    return (coll / "MANIFEST.json").is_file() or (coll / "galaxy.yml").is_file()


def _all_required_installed(dest: Path, required: list[tuple[str, str]]) -> bool:
    return all(collection_is_installed(dest, ns, name) for ns, name in required)


def _cache_matches(dest: Path, digest: str, required: list[tuple[str, str]]) -> bool:
    stamp = dest / STAMP_NAME
    if not stamp.is_file():
        return False
    if stamp.read_text(encoding="utf-8").strip() != digest:
        return False
    if not required:
        return True
    return _all_required_installed(dest, required)


def ensure_collections(
    playbook_root: Path,
    *,
    home: Path | None = None,
    env: dict[str, str] | None = None,
    stderr: TextIO | None = None,
    galaxy_bin: str | None = None,
) -> Path | None:
    """Install or refresh collections into ~/.cache/ansible-plaibook/collections.

    No-op when the playbook tree has no requirements file (tests, bare
    --root fixtures). Never writes to ~/.ansible/collections — that path
    is where a sibling-checkout symlink made `ansible-galaxy` crash with
    ``OSError: Cannot call rmtree on a symbolic link``.
    """
    requirements = Path(playbook_root) / REQUIREMENTS_NAME
    if not requirements.is_file():
        return None

    dest = collections_dir(home)
    # exists() is false for a dangling symlink; is_symlink() is not.
    if dest.is_symlink():
        raise CollectionInstallError(
            f"{dest} is a symlink; plaibook will not install collections "
            f"through it. Remove the symlink so {CACHE_DIRNAME} can own this cache."
        )
    if dest.exists() and not dest.is_dir():
        raise CollectionInstallError(
            f"{dest} exists and is not a directory. Remove it so {CACHE_DIRNAME} can own this cache."
        )
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CollectionInstallError(f"Cannot create collections cache {dest}: {exc}") from exc

    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    required = requirement_collection_keys(requirements)
    stamp = dest / STAMP_NAME
    if _cache_matches(dest, digest, required):
        return dest

    if stderr is not None:
        if stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == digest:
            stderr.write("Updating Ansible collections (cache incomplete)…\n")
        elif stamp.is_file():
            stderr.write("Updating Ansible collections (pin change)…\n")
        else:
            stderr.write(f"Installing Ansible collections (first run, into ~/.cache/{CACHE_DIRNAME}/collections)…\n")
        stderr.flush()

    try:
        command = [
            galaxy_bin or ansible_galaxy_bin(),
            "collection",
            "install",
            "-r",
            str(requirements),
            "-p",
            str(dest),
            "--force",
        ]
        merged = os.environ.copy()
        if env:
            merged.update(env)
        _quiet_git_env(merged)
        merged.setdefault("GIT_TERMINAL_PROMPT", "0")
        merged.setdefault("ANSIBLE_FORCE_COLOR", "0")
        completed = subprocess.run(
            command,
            env=merged,
            capture_output=True,
            text=True,
            timeout=GALAXY_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CollectionInstallError(
            "ansible-galaxy not found next to this interpreter or on PATH. "
            "Reinstall plaibook (`pip install plaibook`); ansible-core is a dependency."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise CollectionInstallError(
            f"ansible-galaxy timed out after {GALAXY_TIMEOUT_SECONDS}s installing collections into {dest}."
        ) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise CollectionInstallError(
            f"ansible-galaxy failed installing collections into {dest}" + (f":\n{detail}" if detail else ".")
        )
    stamp.write_text(digest + "\n", encoding="utf-8")
    return dest


def _quiet_git_env(env: dict[str, str]) -> None:
    """Git collections check out a SHA; silence advice.detachedHead on first run."""
    try:
        count = int(env.get("GIT_CONFIG_COUNT") or 0)
    except ValueError:
        count = 0
    env["GIT_CONFIG_COUNT"] = str(count + 1)
    env[f"GIT_CONFIG_KEY_{count}"] = "advice.detachedHead"
    env[f"GIT_CONFIG_VALUE_{count}"] = "false"
