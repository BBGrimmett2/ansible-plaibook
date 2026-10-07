# -*- coding: utf-8 -*-
"""Update the pipx-managed plaibook install from PyPI or a GitHub ref."""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from plaibook import __version__
from plaibook.playbook import last_run_dir

LOCK_NAME = "updates.lock"
PYPI_JSON_API = "https://pypi.org/pypi/plaibook/json"
GITHUB_REPO = "aknochow/ansible-plaibook"
DOWNLOAD_TIMEOUT_SECONDS = 600
PYPI_TIMEOUT_SECONDS = 30
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2.0


class UpdateError(RuntimeError):
    """Base class for update errors."""


class NetworkError(UpdateError):
    """Network/download failures."""


class VersionError(UpdateError):
    """Version comparison/validation failures."""


def update_lock_path(home: Path | None = None) -> Path:
    """Return path to updates.lock file for serializing update operations."""
    return last_run_dir(home) / LOCK_NAME


@contextmanager
def _exclusive_update_lock(home: Path | None = None) -> Iterator[None]:
    """Serialize concurrent update operations on this machine.

    Pattern from collections.py:_exclusive_collections_lock.
    """
    lock_path = update_lock_path(home)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    except OSError as exc:
        raise UpdateError(f"Cannot open update lock {lock_path}: {exc}") from exc
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise UpdateError(
                "Another plai update is already running. "
                "The lock is released when that process exits."
            ) from exc
        yield
    finally:
        os.close(fd)


def current_version() -> str:
    """Return the currently installed plaibook version."""
    return __version__


def fetch_pypi_latest_version(timeout: int = PYPI_TIMEOUT_SECONDS) -> str:
    """Query PyPI JSON API and return the latest published version.

    Raises:
        NetworkError: If PyPI is unreachable or returns invalid JSON.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = Request(PYPI_JSON_API, headers={"User-Agent": f"plaibook/{__version__}"})
            with urlopen(req, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
                return data["info"]["version"]
        except (HTTPError, URLError) as exc:
            if attempt == MAX_RETRIES:
                raise NetworkError(
                    f"Failed to fetch PyPI metadata after {MAX_RETRIES} attempts: {exc}"
                ) from exc
            wait = RETRY_BACKOFF_BASE ** (attempt - 1)
            time.sleep(wait)
        except (KeyError, json.JSONDecodeError) as exc:
            raise NetworkError(f"PyPI returned invalid JSON: {exc}") from exc
    # Should not reach here
    raise NetworkError("Failed to fetch PyPI metadata")


def _validate_github_ref(ref: str) -> str:
    """Validate and sanitize GitHub ref to prevent path traversal.

    Returns the sanitized ref.
    Raises UpdateError if the ref is invalid.
    """
    ref = ref.strip()
    if not ref:
        raise UpdateError("GitHub ref cannot be empty")

    # A branch name may contain slashes. Reject a leading or trailing
    # slash and any parent-directory segment.
    if ".." in ref or "/" in ref.replace("/", "", 1):
        if ref.startswith("/") or ref.endswith("/") or "../" in ref or "/.." in ref:
            raise UpdateError(f"Invalid GitHub ref (path traversal attempt): {ref}")

    return ref


def pipx_executable() -> str:
    """Return the pipx binary, or raise if this machine cannot upgrade a pipx install."""
    found = shutil.which("pipx")
    if not found:
        raise UpdateError(
            "pipx is not on PATH. Install plaibook with `pipx install plaibook`, then run plai update."
        )
    return found


def _run_pipx(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a pipx command and capture its output."""
    try:
        return subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise UpdateError(f"pipx timed out after {DOWNLOAD_TIMEOUT_SECONDS}s") from exc


_PYPI_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]*$")


def pipx_install_pypi(version: str) -> subprocess.CompletedProcess[str]:
    """Install that exact plaibook release from PyPI, replacing any git source.

    ``pipx upgrade plaibook`` follows the spec pipx already recorded. After
    ``plai update --branch``, that spec is a git ref, so the default path
    must install ``plaibook==VERSION`` instead. ``--no-cache-dir`` is passed
    through because the operator sandbox cannot write pip's cache.
    """
    if not _PYPI_VERSION.fullmatch(version) or ".." in version:
        raise UpdateError(f"Invalid PyPI version: {version}")
    return _run_pipx([
        pipx_executable(),
        "install",
        "--force",
        "--pip-args=--no-cache-dir",
        f"plaibook=={version}",
    ])


def pipx_installed_version() -> str | None:
    """Return the plaibook version inside the pipx venv, not this process."""
    result = _run_pipx([pipx_executable(), "runpip", "plaibook", "show", "plaibook"])
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("Version:"):
            return line.split(":", 1)[1].strip() or None
    return None


def _unreadable_pipx_spec(reason: str) -> UpdateError:
    """An unreadable list is not evidence that the install came from PyPI."""
    return UpdateError(
        "Could not read plaibook's pipx spec "
        f"({reason}), so this command will not treat the install as a PyPI release."
    )


def pipx_package_spec() -> str | None:
    """Return the spec pipx recorded for plaibook.

    A PyPI install is ``plaibook``. ``plai update --branch`` records a git
    URL. None means pipx is not installed, or its list has no plaibook venv.

    Raises UpdateError when pipx is installed but the list cannot be read.
    """
    if shutil.which("pipx") is None:
        return None
    result = _run_pipx([pipx_executable(), "list", "--json"])
    if result.returncode != 0 or not (result.stdout or "").strip():
        raise _unreadable_pipx_spec("pipx list --json failed")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise _unreadable_pipx_spec("pipx list --json was not JSON") from exc
    venvs = payload.get("venvs") if isinstance(payload, dict) else None
    if not isinstance(venvs, dict):
        raise _unreadable_pipx_spec("pipx list --json has no venvs object")
    if "plaibook" not in venvs:
        return None
    entry = venvs.get("plaibook")
    metadata = entry.get("metadata") if isinstance(entry, dict) else None
    main = metadata.get("main_package") if isinstance(metadata, dict) else None
    spec = main.get("package_or_url") if isinstance(main, dict) else None
    if not isinstance(spec, str) or not spec.strip():
        raise _unreadable_pipx_spec("plaibook's package_or_url is missing")
    return spec.strip()


def pipx_spec_is_pypi(spec: str | None) -> bool:
    """True when pipx recorded a PyPI name, or recorded no plaibook venv.

    None is only the no-venv result. An unreadable list raises instead.
    """
    if spec is None:
        return True
    return spec == "plaibook" or spec.startswith("plaibook==")


def pipx_install_git_ref(ref: str) -> subprocess.CompletedProcess[str]:
    """Install a GitHub ref into the pipx environment, replacing any existing plaibook.

    ``pipx upgrade`` cannot switch sources. ``pipx install --force`` records
    this spec and passes ``--no-cache-dir`` to pip. The operator sandbox
    cannot write pip's cache. A later ``plai update`` with no flags installs
    the PyPI release instead, so the git spec does not stick.
    """
    ref = _validate_github_ref(ref)
    git_url = f"git+https://github.com/{GITHUB_REPO}.git@{ref}"
    # Reject user:token@host before the ref separator.
    if "@" in git_url.split("@")[0]:
        raise UpdateError("git URL must not contain embedded credentials")
    return _run_pipx([
        pipx_executable(),
        "install",
        "--force",
        "--pip-args=--no-cache-dir",
        git_url,
    ])


def prompt_confirm(message: str, default: bool = True) -> bool:
    """Ask for Y/n confirmation.

    Returns True if the user confirms, False otherwise.
    When stdin is not a terminal, returns False. Non-interactive updates
    must pass ``--yes``. The default applies only to an empty reply on a
    terminal.
    """
    if not sys.stdin.isatty():
        return False

    prompt_text = f"{message} [{'Y/n' if default else 'y/N'}] "
    try:
        response = input(prompt_text).strip().lower()
        if not response:
            return default
        return response in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()  # newline after ^C
        return False
