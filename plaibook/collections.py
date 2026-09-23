# -*- coding: utf-8 -*-
"""Install collections into a plaibook-owned cache, never ~/.ansible."""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO
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
LOCK_NAME = "collections.lock"
ENV_COLLECTIONS = "ANSIBLE_COLLECTIONS_PATH"
ENV_COLLECTIONS_LEGACY = "ANSIBLE_COLLECTIONS_PATHS"
GALAXY_TIMEOUT_SECONDS = 600
COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# GitHub mirrors for FQCN rows and for galaxy.yml deps (community.general
# pulls community.library_inventory_filtering_v1). Refs are commit SHAs of
# the named release tags, not the tags themselves (tags move). First-run
# install clones these with git (OS trust store) and ``ansible-galaxy
# install --no-deps``. It never calls galaxy.ansible.com — Python's
# OpenSSL on corporate Macs fails that API with ASN1 / NOT_ENOUGH_DATA.
# bump_collection_pins.py does not float these to default-branch HEAD.
GALAXY_GITHUB_MIRRORS: dict[str, tuple[str, str]] = {
    "ansible.posix": (
        "https://github.com/ansible-collections/ansible.posix.git",
        "e98d9a0756458be1ac710988498000973889075c",  # 2.2.2
    ),
    "kubernetes.core": (
        "https://github.com/ansible-collections/kubernetes.core.git",
        "0f472b53e2ee73e11b5f9067ab0826d76183c157",  # 6.5.0
    ),
    "community.general": (
        "https://github.com/ansible-collections/community.general.git",
        "049524674b13ad9782849c427266935c8ec61954",  # 13.4.0
    ),
    "community.library_inventory_filtering_v1": (
        "https://github.com/ansible-collections/community.library_inventory_filtering.git",
        "5f70dd8678885157cb186e8b64382a30cab3e12f",  # 1.1.5
    ),
}


class CollectionInstallError(RuntimeError):
    """Could not install collections-requirements.yml from GitHub."""


def require_commit_sha(url: str, ref: object) -> str:
    """Git collections must pin a full commit. Branches, tags, and HEAD move."""
    shown = redact_git_userinfo(str(url))
    if git_url_has_userinfo(str(url)):
        raise CollectionInstallError(
            f"git collection {shown} must not embed credentials in the URL"
        )
    if _cleartext_http_git(str(url)):
        raise CollectionInstallError(
            f"git collection {shown} must use HTTPS (or ssh/file), not plaintext HTTP"
        )
    if isinstance(ref, str) and COMMIT_SHA_RE.fullmatch(ref.lower()):
        return ref.lower()
    raise CollectionInstallError(f"git collection {shown} must pin a 40-character commit SHA")


# Password-bearing userinfo for every accepted scheme, including git+ssh://
# and file://. The previous https/git+ -only pattern left ssh://user:PASSWORD@
# and git+ssh://user:PASSWORD@ unredacted for `git remote add`.
_GIT_PASSWORD_USERINFO_RE = re.compile(r"(?i)((?:git\+)?(?:https?|ssh|file|git)://)[^/\s:@]+:[^/\s@]*@")
# Tokens often appear as https://TOKEN@host with no password component.
_GIT_AUTHORITY_USERINFO_RE = re.compile(r"(?i)((?:git\+)?(?:https?|file)://)[^/\s:@]+@")
_BASIC_AUTH_RE = re.compile(r"(?i)(AUTHORIZATION:\s*basic\s+)\S+")
_GIT_PLUS_PREFIX = "git+"


def _git_url_without_plus(url: str) -> str:
    stripped = url.strip()
    if stripped[:4].lower() == _GIT_PLUS_PREFIX:
        return stripped[4:]
    return stripped


def redact_git_userinfo(text: str) -> str:
    """Drop userinfo and HTTP basic headers so errors cannot log credentials."""
    text = _GIT_PASSWORD_USERINFO_RE.sub(r"\1", text)
    text = _GIT_AUTHORITY_USERINFO_RE.sub(r"\1", text)
    return _BASIC_AUTH_RE.sub(r"\1[redacted]", text)


def git_url_has_userinfo(url: str) -> bool:
    """True when a git URL embeds username/password/token in the authority.

    ``ssh://git@host/repo.git`` (username, no password) is the normal SSH
    form and is allowed. ``ssh://user:PASSWORD@host`` is not. HTTP(S) and
    ``file://`` reject any userinfo, including token-as-username.
    """
    parsed = urlparse(_git_url_without_plus(url))
    scheme = parsed.scheme.lower()
    if parsed.password:
        return True
    if parsed.username and scheme in {"http", "https", "file"}:
        return True
    return _GIT_PASSWORD_USERINFO_RE.search(url) is not None or _GIT_AUTHORITY_USERINFO_RE.search(url) is not None


def _cleartext_http_git(url: str) -> bool:
    rest = url.strip().removeprefix("git+").removeprefix("GIT+")
    return rest.lower().startswith("http://")


def _git_source_name_ok(name: str) -> bool:
    """Git rows are an HTTPS/ssh/file URL or a local path, never HTTP or a bare FQCN."""
    if name.startswith(("/", "./", "../")):
        return True
    if _cleartext_http_git(name):
        return False
    rest = name.removeprefix("git+")
    if rest.startswith(("https://", "ssh://", "file://")):
        return True
    return name.startswith("git@")


def ansible_galaxy_bin() -> str:
    return ansible_tool_bin("ansible-galaxy")


def collections_dir(home: Path | None = None) -> Path:
    return last_run_dir(home) / COLLECTIONS_DIRNAME


def collections_lock_path(home: Path | None = None) -> Path:
    """Lock file beside the cache dir, not inside ``-p dest`` (galaxy --force)."""
    return last_run_dir(home) / LOCK_NAME


@contextmanager
def _exclusive_collections_lock(home: Path | None = None) -> Iterator[None]:
    """Serialize cache check, ansible-galaxy, and stamp write on this machine.

    POSIX ``fcntl.flock`` only. Windows is not a supported plaibook host.
    """
    lock_path = collections_lock_path(home)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    except OSError as exc:
        raise CollectionInstallError(f"Cannot open collections lock {lock_path}: {exc}") from exc
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError as exc:
            raise CollectionInstallError(f"Cannot lock collections cache {lock_path}: {exc}") from exc
        yield
    finally:
        os.close(fd)


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


def _load_requirement_rows(requirements: Path) -> list[dict]:
    """Parse collections-requirements.yml. Malformed files must not look empty."""
    data = yaml.safe_load(requirements.read_bytes())
    if data is None:
        return []
    if not isinstance(data, dict):
        raise CollectionInstallError(
            f"{requirements} must be a mapping with a collections list, not {type(data).__name__}"
        )
    if "collections" not in data:
        raise CollectionInstallError(f"{requirements} must contain a collections list")
    cols = data["collections"]
    if cols is None:
        return []
    if not isinstance(cols, list):
        raise CollectionInstallError(f"{requirements} collections: must be a list, not {type(cols).__name__}")
    rows: list[dict] = []
    for index, col in enumerate(cols):
        if not isinstance(col, dict):
            raise CollectionInstallError(
                f"{requirements} collections[{index}] must be a mapping, not {type(col).__name__}"
            )
        _validate_requirement_row(requirements, index, col)
        rows.append(col)
    return rows


def _validate_requirement_row(requirements: Path, index: int, col: dict) -> None:
    """Reject rows the installer would skip and then stamp as a complete cache."""
    where = f"{requirements} collections[{index}]"
    name = col.get("name")
    if not isinstance(name, str) or not name.strip():
        raise CollectionInstallError(f"{where} must have a non-empty string name")
    name = name.strip()
    row_type = col.get("type")
    url_like = name.startswith(("https://", "http://", "git+", "git@", "file://"))
    if row_type != "git" and not url_like:
        return
    if row_type not in (None, "git"):
        raise CollectionInstallError(f"{where} git source type must be git, not {row_type!r}")
    if _cleartext_http_git(name):
        raise CollectionInstallError(
            f"{where}: git collection {redact_git_userinfo(name)} must use HTTPS (or ssh/file), not plaintext HTTP"
        )
    if git_url_has_userinfo(name):
        raise CollectionInstallError(
            f"{where}: git collection {redact_git_userinfo(name)} must not embed credentials in the URL"
        )
    if not _git_source_name_ok(name):
        raise CollectionInstallError(
            f"{where} git source must be an https, ssh, git@, or file URL, not {redact_git_userinfo(name)!r}"
        )
    url = name.removeprefix("git+")
    try:
        require_commit_sha(url, col.get("version"))
    except CollectionInstallError as exc:
        raise CollectionInstallError(f"{where}: {exc}") from exc


def requirement_collection_keys(requirements: Path) -> list[tuple[str, str]]:
    """Named collections the stamp must contain — not a raw directory count."""
    cols = _load_requirement_rows(requirements)
    keys: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for col in cols:
        key = collection_key_from_requirement(col)
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def _runtime_required_keys(required: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Include GitHub-mirror deps that Galaxy would have pulled."""
    keys = list(required)
    seen = set(keys)
    if ("community", "general") in seen:
        extra = ("community", "library_inventory_filtering_v1")
        if extra not in seen:
            keys.append(extra)
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
    check = _runtime_required_keys(required)
    if not check:
        return True
    return _all_required_installed(dest, check)


def _galaxy_env(home: Path | None, extra: dict[str, str] | None) -> dict[str, str]:
    merged = os.environ.copy()
    if extra:
        merged.update(extra)
    merge_collections_path(merged, home=home)
    _quiet_git_env(merged)
    merged.setdefault("GIT_TERMINAL_PROMPT", "0")
    merged.setdefault("ANSIBLE_FORCE_COLOR", "0")
    return merged


def _clone_at_ref(url: str, ref: str, dest: Path, env: dict[str, str] | None = None) -> None:
    sha = require_commit_sha(url, ref)
    safe_url = redact_git_userinfo(url)
    last_error: Exception | None = None
    for attempt in range(1, 4):
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        try:
            subprocess.run(
                ["git", "-c", "advice.detachedHead=false", "init", "-b", "main"],
                cwd=dest,
                env=env,
                check=True,
                timeout=GALAXY_TIMEOUT_SECONDS,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "remote", "add", "origin", safe_url],
                cwd=dest,
                env=env,
                check=True,
                timeout=GALAXY_TIMEOUT_SECONDS,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "-c", "advice.detachedHead=false", "fetch", "--depth", "1", "origin", sha],
                cwd=dest,
                env=env,
                check=True,
                timeout=GALAXY_TIMEOUT_SECONDS,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "-c", "advice.detachedHead=false", "checkout", "FETCH_HEAD"],
                cwd=dest,
                env=env,
                check=True,
                timeout=GALAXY_TIMEOUT_SECONDS,
                capture_output=True,
                text=True,
            )
            got = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=dest,
                env=env,
                check=True,
                timeout=GALAXY_TIMEOUT_SECONDS,
                capture_output=True,
                text=True,
            )
            checked = got.stdout.strip().lower()
            if checked != sha:
                raise CollectionInstallError(f"git checkout of {safe_url} resolved to {checked}, not pinned {sha}")
            return
        except CollectionInstallError:
            raise
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            last_error = exc
            time.sleep(attempt * 4)
    detail = redact_git_userinfo(str(last_error))
    raise CollectionInstallError(f"git fetch failed for {safe_url}@{sha}: {detail}")


def _build_collection_archive(galaxy: str, source: Path, output_dir: Path, env: dict[str, str]) -> Path:
    """Turn a galaxy.yml checkout into the .tar.gz ansible-galaxy install expects."""
    output_dir.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            galaxy,
            "collection",
            "build",
            "--force",
            "--output-path",
            str(output_dir),
        ],
        cwd=source,
        env=env,
        capture_output=True,
        text=True,
        timeout=GALAXY_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise CollectionInstallError(
            f"ansible-galaxy collection build {source} failed" + (f":\n{detail}" if detail else ".")
        )
    archives = sorted(output_dir.glob("*.tar.gz"))
    if len(archives) != 1:
        raise CollectionInstallError(
            f"ansible-galaxy collection build {source} produced {len(archives)} archives, expected 1"
        )
    return archives[0]


def _install_from_dir(galaxy: str, source: Path, dest: Path, env: dict[str, str]) -> None:
    with tempfile.TemporaryDirectory(prefix="plaibook-coll-build-") as tmp:
        archive = _build_collection_archive(galaxy, source, Path(tmp), env)
        completed = subprocess.run(
            [
                galaxy,
                "collection",
                "install",
                str(archive),
                "-p",
                str(dest),
                "--force",
                "--no-deps",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=GALAXY_TIMEOUT_SECONDS,
            check=False,
        )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise CollectionInstallError(
            f"ansible-galaxy install {archive} --no-deps failed" + (f":\n{detail}" if detail else ".")
        )


def install_git_sources(
    galaxy: str,
    dest: Path,
    cols: list[dict],
    env: dict[str, str],
) -> None:
    for col in cols:
        name = col.get("name")
        if not isinstance(name, str) or not name.strip():
            raise CollectionInstallError("collection requirement is missing a non-empty string name")
        name = name.strip()
        is_git = col.get("type") == "git" or name.startswith(("https://", "http://", "git+", "git@", "file://"))
        if not is_git:
            continue
        url = name.removeprefix("git+")
        ref = require_commit_sha(url, col.get("version"))
        with tempfile.TemporaryDirectory(prefix="plaibook-coll-") as tmp:
            checkout = Path(tmp) / "collection"
            _clone_at_ref(url, ref, checkout, env)
            _install_from_dir(galaxy, checkout, dest, env)


def install_galaxy_github_mirrors(
    galaxy: str,
    dest: Path,
    env: dict[str, str],
    *,
    needed: list[tuple[str, str]] | None = None,
) -> None:
    want = set(needed) if needed is not None else None
    for fqn, (url, ref) in GALAXY_GITHUB_MIRRORS.items():
        ns, name = fqn.split(".", 1)
        if want is not None and (ns, name) not in want:
            continue
        if collection_is_installed(dest, ns, name):
            continue
        with tempfile.TemporaryDirectory(prefix="plaibook-coll-") as tmp:
            checkout = Path(tmp) / "collection"
            _clone_at_ref(url, ref, checkout, env)
            _install_from_dir(galaxy, checkout, dest, env)


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
    with _exclusive_collections_lock(home):
        if _cache_matches(dest, digest, required):
            return dest

        if stderr is not None:
            if stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == digest:
                stderr.write("Updating Ansible collections (cache incomplete)…\n")
            elif stamp.is_file():
                stderr.write("Updating Ansible collections (pin change)…\n")
            else:
                stderr.write(
                    "Installing Ansible collections from GitHub (first run, "
                    f"no galaxy.ansible.com, into ~/.cache/{CACHE_DIRNAME}/collections)…\n"
                )
            stderr.flush()

        try:
            galaxy = galaxy_bin or ansible_galaxy_bin()
        except FileNotFoundError as exc:
            raise CollectionInstallError(
                "ansible-galaxy not found next to this interpreter or on PATH. "
                "Reinstall plaibook (`pip install plaibook`); ansible-core is a dependency."
            ) from exc

        merged = _galaxy_env(home, env)
        needed = _runtime_required_keys(required)
        cols = _load_requirement_rows(requirements)
        try:
            install_git_sources(galaxy, dest, cols, merged)
            install_galaxy_github_mirrors(galaxy, dest, merged, needed=needed)
        except CollectionInstallError:
            raise
        except subprocess.TimeoutExpired as exc:
            raise CollectionInstallError(
                f"ansible-galaxy timed out after {GALAXY_TIMEOUT_SECONDS}s installing collections into {dest}."
            ) from exc
        except (subprocess.SubprocessError, OSError) as exc:
            raise CollectionInstallError(f"GitHub collection install failed into {dest}: {exc}") from exc
        if needed and not _all_required_installed(dest, needed):
            missing = [f"{ns}.{name}" for ns, name in needed if not collection_is_installed(dest, ns, name)]
            raise CollectionInstallError(f"GitHub collection install into {dest} still missing {', '.join(missing)}")
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
