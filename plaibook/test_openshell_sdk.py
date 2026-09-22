# -*- coding: utf-8 -*-
"""OpenShell SDK pin: reject 0.0.0a0 and install the collection's range."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from plaibook.openshell_sdk import (
    SDK_SPEC,
    OpenshellSdkError,
    ensure_openshell_sdk,
    version_satisfies,
)


@pytest.mark.parametrize(
    ("version", "ok"),
    [
        ("0.0.0a0", False),
        ("0.0.115", False),
        ("0.0.116", True),
        ("0.0.119", True),
        ("0.0.119+local", True),
        ("0.0.120", False),
        ("0.0.116rc1", False),
    ],
)
def test_version_satisfies_pin(version, ok):
    assert version_satisfies(version) is ok


def test_ensure_openshell_sdk_skips_pip_when_already_in_range(monkeypatch):
    calls = []
    monkeypatch.setattr("plaibook.openshell_sdk.sdk_satisfies", lambda: True)
    monkeypatch.setattr("plaibook.openshell_sdk.subprocess.run", lambda *a, **k: calls.append(a))
    ensure_openshell_sdk()
    assert calls == []


def test_ensure_openshell_sdk_replaces_editable_stub(monkeypatch):
    recorded = []
    checks = {"n": 0}

    def fake_satisfies():
        checks["n"] += 1
        return checks["n"] > 1

    def fake_run(cmd, **kwargs):
        recorded.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("plaibook.openshell_sdk.sdk_satisfies", fake_satisfies)
    monkeypatch.setattr("plaibook.openshell_sdk.subprocess.run", fake_run)
    ensure_openshell_sdk(stderr=None)
    assert recorded[0][-3:] == ["pip", "uninstall", "-y"] or "uninstall" in recorded[0]
    assert recorded[0][1:4] == ["-m", "pip", "uninstall"]
    assert recorded[1][1:4] == ["-m", "pip", "install"]
    assert recorded[1][-1] == SDK_SPEC


def test_ensure_openshell_sdk_surfaces_pip_failure(monkeypatch):
    monkeypatch.setattr("plaibook.openshell_sdk.sdk_satisfies", lambda: False)

    def fake_run(cmd, **kwargs):
        if "uninstall" in cmd:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="no matching distribution")

    monkeypatch.setattr("plaibook.openshell_sdk.subprocess.run", fake_run)
    with pytest.raises(OpenshellSdkError, match="no matching distribution"):
        ensure_openshell_sdk()
