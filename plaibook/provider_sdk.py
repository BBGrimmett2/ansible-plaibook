# -*- coding: utf-8 -*-
"""Install the Python SDK for the configured review provider.

aknochow.openai / claude / gemini import their SDKs in the same
interpreter that runs Ansible modules. A Python 3.10 ``plai`` re-execs
into ``~/.cache/ansible-plaibook/sandbox-runtime``, which only has
plaibook and OpenShell until this step runs.

Installs use checked-in hashed requirements (``plaibook/hashed/``) with
``pip install --require-hashes``. Pins stay inside the collection
ranges in ``execution-environment.yml``.
"""

from __future__ import annotations

import subprocess
import sys
from typing import TextIO

from plaibook.pip_hashed import pinned_versions, pip_install_hashed_argv

# (import-name, distribution-name). Import names are what find_spec must see.
FAMILY_REQUIREMENTS: dict[str, tuple[tuple[str, str], ...]] = {
    "openai": (("openai", "openai"),),
    "claude": (
        ("anthropic", "anthropic"),
        ("claude_agent_sdk", "claude-agent-sdk"),
    ),
    "gemini": (("google.genai", "google-genai"),),
    "cursor": (("cursor", "cursor-sdk"),),
}
FAMILY_HASHED_FILE: dict[str, str] = {
    "openai": "openai-requirements.txt",
    "claude": "claude-requirements.txt",
    "gemini": "gemini-requirements.txt",
    "cursor": "cursor-requirements.txt",
}


class ProviderSdkError(RuntimeError):
    """Could not put the provider SDK on this interpreter."""


def ensure_provider_sdk(
    family: str | None,
    python: str | None = None,
    *,
    stderr: TextIO | None = None,
) -> None:
    """pip-install missing or off-pin provider SDKs into ``python``."""
    key = (family or "").strip()
    requirements = FAMILY_REQUIREMENTS.get(key)
    hashed_file = FAMILY_HASHED_FILE.get(key)
    if not requirements or not hashed_file:
        return
    try:
        pins = pinned_versions(hashed_file)
    except OSError as exc:
        raise ProviderSdkError(f"cannot read hashed provider requirements: {exc}") from exc
    exe = python or sys.executable
    missing: list[tuple[str, str, str]] = []
    for mod, dist in requirements:
        expected = pins.get(_normalize_dist(dist))
        if expected is None:
            raise ProviderSdkError(f"{hashed_file} has no pin for {dist}")
        if not _requirement_satisfied(exe, mod, dist, expected):
            missing.append((mod, dist, expected))
    if not missing:
        return
    out = stderr if stderr is not None else sys.stderr
    label = ", ".join(f"{dist}=={ver}" for _mod, dist, ver in missing)
    out.write(f"Installing {key} provider SDK ({label}) for {exe}…\n")
    out.flush()
    try:
        argv = pip_install_hashed_argv(exe, hashed_file)
    except OSError as exc:
        raise ProviderSdkError(f"cannot read hashed provider requirements: {exc}") from exc
    install = _run(argv, timeout=300)
    if install.returncode != 0:
        detail = ((install.stderr or install.stdout or "").strip() or install.returncode)
        if isinstance(detail, str) and len(detail) > 2000:
            detail = detail[-2000:]
        raise ProviderSdkError(f"pip install --require-hashes -r {hashed_file} failed: {detail}")
    still = [
        dist
        for _mod, dist, ver in missing
        if not _requirement_satisfied(exe, _mod, dist, ver)
    ]
    if still:
        raise ProviderSdkError(
            f"pip install finished but {exe} still cannot import {', '.join(still)} at the hashed pin."
        )


def _normalize_dist(name: str) -> str:
    return name.replace("_", "-").lower()


def _requirement_satisfied(python: str, import_name: str, dist_name: str, expected: str | None) -> bool:
    if not expected:
        return False
    if not _module_present(python, import_name):
        return False
    installed = _dist_version(python, dist_name)
    return installed == expected


def _module_present(python: str, name: str) -> bool:
    probe = (
        "import importlib.util, sys\n"
        f"sys.exit(0 if importlib.util.find_spec({name!r}) else 1)\n"
    )
    if _same_executable(python):
        import importlib.util

        return importlib.util.find_spec(name) is not None
    completed = _run([python, "-c", probe], timeout=60)
    return completed.returncode == 0


def _dist_version(python: str, dist_name: str) -> str | None:
    if _same_executable(python):
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version(dist_name)
        except PackageNotFoundError:
            return None
    probe = (
        "from importlib.metadata import PackageNotFoundError, version\n"
        "import sys\n"
        "try:\n"
        f"    print(version({dist_name!r}))\n"
        "except PackageNotFoundError:\n"
        "    sys.exit(1)\n"
    )
    completed = _run([python, "-c", probe], timeout=60)
    if completed.returncode != 0:
        return None
    lines = completed.stdout.strip().splitlines()
    return lines[-1] if lines else None


def _same_executable(python: str) -> bool:
    from pathlib import Path

    try:
        return Path(python).resolve() == Path(sys.executable).resolve()
    except OSError:
        return python == sys.executable


def _run(argv: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProviderSdkError(f"command timed out after {timeout}s: {' '.join(argv[:4])}") from exc
    except OSError as exc:
        raise ProviderSdkError(f"cannot run {argv[0]}: {exc}") from exc
