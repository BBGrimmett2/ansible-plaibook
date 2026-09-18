# -*- coding: utf-8 -*-
"""Locate the playbook tree (checkout or bundled share) and run ansible-playbook."""

from __future__ import annotations

import math
import os
import secrets
import shutil
import signal
import string
import subprocess
import sys
from pathlib import Path

PLAYBOOK_NAME = "review.yml"
ANSIBLE_CFG_NAME = "ansible.cfg"
ENV_ROOT = "PLAIBOOK_ROOT"
ENV_TIMEOUT = "PLAIBOOK_PLAYBOOK_TIMEOUT"
DEFAULT_PLAYBOOK_TIMEOUT_SECONDS = 3600
RUN_ID_CHARS = string.ascii_letters + string.digits
RUN_ID_LENGTH = 16
CACHE_DIRNAME = "ansible-plaibook"


class PlaybookNotFoundError(FileNotFoundError):
    """review.yml could not be located from this install."""


class PlaybookTimeoutError(TimeoutError):
    """ansible-playbook exceeded PLAIBOOK_PLAYBOOK_TIMEOUT."""

    def __init__(self, seconds: float, command: list[str]):
        self.seconds = seconds
        self.command = command
        super().__init__(
            f"ansible-playbook exceeded {seconds:.0f}s timeout. "
            f"Set {ENV_TIMEOUT} to raise the limit (seconds)."
        )


def generate_run_id() -> str:
    """Match the playbook's password-lookup run_id alphabet and length."""
    return "".join(secrets.choice(RUN_ID_CHARS) for _ in range(RUN_ID_LENGTH))


def last_run_dir(home: Path | None = None) -> Path:
    root = home if home is not None else Path.home()
    return root / ".cache" / CACHE_DIRNAME


def last_run_path(run_id: str, home: Path | None = None) -> Path:
    return last_run_dir(home) / f"last_run.{run_id}.json"


def last_run_canonical_path(home: Path | None = None) -> Path:
    """Last-write-wins sibling of last_run.<run_id>.json."""
    return last_run_dir(home) / "last_run.json"


def bundled_playbook_root(package_dir: Path | None = None) -> Path:
    """Playbook tree vendored into the wheel at plaibook/share/."""
    here = package_dir if package_dir is not None else Path(__file__).resolve().parent
    return Path(here).resolve() / "share"


def find_playbook_root(
    start: Path | None = None,
    env: dict[str, str] | None = None,
    *,
    package_dir: Path | None = None,
) -> Path:
    """Find the tree that contains review.yml + ansible.cfg.

    ``pip install plaibook && plai review`` uses this install: an
    editable checkout (package parents) or the wheel's bundled share.
    ``PLAIBOOK_ROOT`` is a last resort when this install has no
    playbook, not a hijack of a working pip install. ``--root`` is the
    checkout override (handled by the CLI). cwd is never searched:
    a reviewed repo must not supply review.yml.
    ``start`` is accepted for call-site compatibility and ignored.
    """
    _ = start
    environ = os.environ if env is None else env
    here = (package_dir or Path(__file__).resolve().parent).resolve()
    seen: set[Path] = set()
    for candidate in here.parents:
        if candidate in seen:
            continue
        seen.add(candidate)
        if _is_playbook_root(candidate):
            return candidate

    bundled = bundled_playbook_root(here)
    if _is_playbook_root(bundled):
        return bundled

    explicit = environ.get(ENV_ROOT, "").strip()
    if explicit:
        root = Path(explicit).expanduser().resolve()
        if _is_playbook_root(root):
            return root
        raise PlaybookNotFoundError(
            f"{ENV_ROOT}={root} does not contain {PLAYBOOK_NAME} and {ANSIBLE_CFG_NAME}"
        )

    raise PlaybookNotFoundError(
        "Could not find review.yml. Reinstall plaibook (`pip install plaibook`) "
        f"or pass --root / set {ENV_ROOT} to an ansible-plaibook checkout."
    )


def _is_playbook_root(path: Path) -> bool:
    return (path / PLAYBOOK_NAME).is_file() and (path / ANSIBLE_CFG_NAME).is_file()


def ansible_tool_bin(name: str) -> str:
    """Prefer *name* next to this interpreter, even if python is a symlink.

    ``Path.resolve()`` follows ``.venv/bin/python`` into ``/usr/bin``, so the
    sibling lookup would miss ``.venv/bin/ansible-playbook`` and fall through
    to an unrelated PATH binary.
    """
    exe = Path(sys.executable)
    candidates = [exe.parent / name, exe.resolve().parent / name]
    seen: set[Path] = set()
    for sibling in candidates:
        if sibling in seen:
            continue
        seen.add(sibling)
        if sibling.is_file() and os.access(sibling, os.X_OK):
            return str(sibling)
    found = shutil.which(name)
    if found:
        return found
    raise FileNotFoundError(
        f"{name} not found next to this interpreter or on PATH. "
        "Reinstall plaibook (`pip install plaibook`); ansible-core is a dependency."
    )


def ansible_playbook_bin() -> str:
    return ansible_tool_bin("ansible-playbook")


def build_ansible_command(
    *,
    extra_vars: dict,
    playbook_root: Path,
    ansible_bin: str | None = None,
    verbosity: int = 0,
) -> list[str]:
    import json

    playbook = playbook_root / PLAYBOOK_NAME
    command = [ansible_bin or ansible_playbook_bin(), str(playbook)]
    if verbosity > 0:
        command.append("-" + ("v" * min(int(verbosity), 4)))
    command.extend(["-e", json.dumps(extra_vars, separators=(",", ":"))])
    return command


def playbook_timeout_seconds(env: dict[str, str] | None = None) -> float:
    """Seconds ansible-playbook may run before the CLI kills the process group."""
    environ = os.environ if env is None else env
    raw = (environ.get(ENV_TIMEOUT) or "").strip()
    if not raw:
        return float(DEFAULT_PLAYBOOK_TIMEOUT_SECONDS)
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{ENV_TIMEOUT}={raw!r} must be a positive number of seconds") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{ENV_TIMEOUT}={raw!r} must be a positive finite number of seconds")
    return value


def _kill_process_group(proc: subprocess.Popen[str]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def run_ansible_playbook(
    command: list[str],
    *,
    playbook_root: Path,
    verbose: bool,
    env: dict[str, str] | None = None,
    home: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ansible-playbook. Quiet mode captures output; -v inherits the TTY."""
    from plaibook.collections import merge_collections_path

    timeout = playbook_timeout_seconds()
    merged = os.environ.copy()
    if env:
        merged.update(env)
    merged["ANSIBLE_CONFIG"] = str(playbook_root / ANSIBLE_CFG_NAME)
    merge_collections_path(merged, home=home)
    kwargs: dict = {
        "args": command,
        "env": merged,
        "text": True,
        "start_new_session": True,
    }
    if not verbose:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    proc = subprocess.Popen(**kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill_process_group(proc)
        raise PlaybookTimeoutError(timeout, command) from exc
    return subprocess.CompletedProcess(command, proc.returncode, stdout or "", stderr or "")
