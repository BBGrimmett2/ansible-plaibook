# -*- coding: utf-8 -*-
"""Install the Python SDK for the configured review provider.

aknochow.openai / claude / gemini import their SDKs in the same
interpreter that runs Ansible modules. A Python 3.10 ``plai`` re-execs
into ``~/.cache/ansible-plaibook/sandbox-runtime``, which only has
plaibook and OpenShell until this step runs. Pins match the collections'
requirements.txt and execution-environment.yml.
"""

from __future__ import annotations

import subprocess
import sys
from typing import TextIO

# (import-name, pip-spec). Import names are what find_spec must see.
FAMILY_REQUIREMENTS: dict[str, tuple[tuple[str, str], ...]] = {
    "openai": (("openai", "openai>=1.0.0,<4.0.0"),),
    "claude": (
        ("anthropic", "anthropic[vertex]>=0.84.0"),
        ("claude_agent_sdk", "claude-agent-sdk>=0.2.144"),
    ),
    "gemini": (("google.genai", "google-genai>=1.0.0"),),
    "cursor": (("cursor", "cursor-sdk>=1.0.31,<2.0.0"),),
}


class ProviderSdkError(RuntimeError):
    """Could not put the provider SDK on this interpreter."""


def ensure_provider_sdk(
    family: str | None,
    python: str | None = None,
    *,
    stderr: TextIO | None = None,
) -> None:
    """pip-install missing provider SDKs into ``python`` (this interpreter by default)."""
    key = (family or "").strip()
    requirements = FAMILY_REQUIREMENTS.get(key)
    if not requirements:
        return
    exe = python or sys.executable
    missing = [(mod, spec) for mod, spec in requirements if not _module_present(exe, mod)]
    if not missing:
        return
    out = stderr if stderr is not None else sys.stderr
    specs = [spec for _mod, spec in missing]
    out.write(f"Installing {key} provider SDK ({', '.join(specs)}) for {exe}…\n")
    out.flush()
    install = _run([exe, "-m", "pip", "install", "--disable-pip-version-check", *specs], timeout=300)
    if install.returncode != 0:
        detail = ((install.stderr or install.stdout or "").strip() or install.returncode)
        if isinstance(detail, str) and len(detail) > 2000:
            detail = detail[-2000:]
        raise ProviderSdkError(f"pip install of {', '.join(specs)} failed: {detail}")
    still = [mod for mod, _spec in missing if not _module_present(exe, mod)]
    if still:
        raise ProviderSdkError(
            f"pip install finished but {exe} still cannot import {', '.join(still)}."
        )


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
