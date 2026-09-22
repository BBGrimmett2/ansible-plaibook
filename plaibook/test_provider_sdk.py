# -*- coding: utf-8 -*-
"""Provider SDK install: skip when importable, pip the collection pin otherwise."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from plaibook.provider_sdk import ProviderSdkError, ensure_provider_sdk


def test_unknown_family_is_noop(monkeypatch):
    calls = []
    monkeypatch.setattr("plaibook.provider_sdk.subprocess.run", lambda *a, **k: calls.append(a))
    ensure_provider_sdk(None)
    ensure_provider_sdk("claude_cli")
    assert calls == []


def test_skips_pip_when_importable(monkeypatch):
    calls = []
    monkeypatch.setattr("plaibook.provider_sdk._module_present", lambda _py, _name: True)
    monkeypatch.setattr("plaibook.provider_sdk.subprocess.run", lambda *a, **k: calls.append(a))
    ensure_provider_sdk("openai")
    assert calls == []


def test_installs_missing_openai_spec(monkeypatch):
    recorded = []
    present = {"n": 0}

    def fake_present(_py, name):
        if name != "openai":
            return True
        present["n"] += 1
        return present["n"] > 1

    def fake_run(cmd, **kwargs):
        recorded.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("plaibook.provider_sdk._module_present", fake_present)
    monkeypatch.setattr("plaibook.provider_sdk.subprocess.run", fake_run)
    ensure_provider_sdk("openai", stderr=None)
    assert recorded[0][1:4] == ["-m", "pip", "install"]
    assert "openai>=1.0.0,<4.0.0" in recorded[0]


def test_installs_both_claude_specs_when_missing(monkeypatch):
    recorded = []
    state = {"installed": False}

    def fake_present(_py, _name):
        return state["installed"]

    def fake_run(cmd, **kwargs):
        recorded.append(list(cmd))
        state["installed"] = True
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("plaibook.provider_sdk._module_present", fake_present)
    monkeypatch.setattr("plaibook.provider_sdk.subprocess.run", fake_run)
    ensure_provider_sdk("claude", stderr=None)
    assert recorded[0][1:4] == ["-m", "pip", "install"]
    assert "anthropic[vertex]>=0.84.0" in recorded[0]
    assert "claude-agent-sdk>=0.2.144" in recorded[0]


def test_pip_failure_surfaces(monkeypatch):
    monkeypatch.setattr("plaibook.provider_sdk._module_present", lambda _py, _name: False)
    monkeypatch.setattr(
        "plaibook.provider_sdk.subprocess.run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="no matching distribution"),
    )
    with pytest.raises(ProviderSdkError, match="no matching distribution"):
        ensure_provider_sdk("openai")
