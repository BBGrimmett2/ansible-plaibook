# -*- coding: utf-8 -*-
"""Install the OpenShell SDK into the interpreter that runs Ansible modules."""

from __future__ import annotations

import re
import subprocess
import sys
from typing import TextIO

# Same pin as aknochow.openshell (OPENSHELL_SDK_SPEC). 0.0.116 is the
# first release this collection calls; 0.0.120 is excluded so a later
# 0.0.x API break does not get installed automatically.
SDK_SPEC = "openshell>=0.0.116,<0.0.120"
_MIN = (0, 0, 116)
_MAX = (0, 0, 120)
_RELEASE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:\+.*)?$")


class OpenshellSdkError(RuntimeError):
    """Could not put a compatible openshell SDK on this interpreter."""


def release_tuple(version: str) -> tuple[int, int, int] | None:
    """Return (major, minor, patch) for a final release. Pre-releases are None."""
    match = _RELEASE.fullmatch(version.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def version_satisfies(version: str) -> bool:
    parsed = release_tuple(version)
    return parsed is not None and _MIN <= parsed < _MAX


def sdk_satisfies() -> bool:
    """True when this process can import SandboxClient from an in-range SDK."""
    try:
        from importlib.metadata import version

        from openshell import SandboxClient
    except Exception:
        return False
    if SandboxClient is None:
        return False
    try:
        installed = version("openshell")
    except Exception:
        return False
    return version_satisfies(installed)


def _interpreter_satisfies(python: str) -> bool:
    if python == sys.executable:
        return sdk_satisfies()
    probe = "import sys\nfrom plaibook.openshell_sdk import sdk_satisfies\nsys.exit(0 if sdk_satisfies() else 1)\n"
    completed = subprocess.run(
        [python, "-c", probe],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return completed.returncode == 0


def ensure_openshell_sdk(
    python: str | None = None,
    *,
    stderr: TextIO | None = None,
) -> None:
    """pip-install the pinned SDK when this interpreter's copy is missing or wrong.

    ``pip install openshell --upgrade`` leaves an editable ``0.0.0a0`` in
    place and reports it already satisfied. Uninstall first so the pin
    actually lands.
    """
    exe = python or sys.executable
    if _interpreter_satisfies(exe):
        return
    out = stderr if stderr is not None else sys.stderr
    out.write(f"Installing OpenShell SDK ({SDK_SPEC}) for {exe}…\n")
    out.flush()
    uninstall = subprocess.run(
        [exe, "-m", "pip", "uninstall", "-y", "openshell"],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if uninstall.returncode != 0:
        detail = (uninstall.stderr or uninstall.stdout or "").strip()
        raise OpenshellSdkError(f"pip uninstall openshell failed: {detail or uninstall.returncode}")
    install = subprocess.run(
        [exe, "-m", "pip", "install", SDK_SPEC],
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if install.returncode != 0:
        detail = (install.stderr or install.stdout or "").strip()
        raise OpenshellSdkError(f"pip install '{SDK_SPEC}' failed: {detail or install.returncode}")
    if not _interpreter_satisfies(exe):
        raise OpenshellSdkError(
            f"pip install '{SDK_SPEC}' finished but {exe} still cannot import openshell.SandboxClient from that range."
        )
