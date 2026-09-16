# -*- coding: utf-8 -*-
"""CI collection installer: named cache keys and subprocess timeouts."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from plaibook.collections import GALAXY_TIMEOUT_SECONDS, requirement_collection_keys

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_ci_install():
    spec = importlib.util.spec_from_file_location(
        "ci_install_collections",
        REPO_ROOT / "scripts" / "ci-install-collections.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_repo_requirements_keys_are_named_not_a_raw_count():
    keys = requirement_collection_keys(REPO_ROOT / "collections-requirements.yml")
    assert ("aknochow", "openshell") in keys
    assert ("aknochow", "cursor") in keys
    assert ("community", "general") in keys
    assert ("kubernetes", "core") in keys
    assert ("ansible", "posix") in keys
    assert len(keys) == 8


def test_install_from_dir_passes_galaxy_timeout(monkeypatch, tmp_path):
    mod = _load_ci_install()
    recorded = {}

    def fake_run(cmd, **kwargs):
        recorded["cmd"] = list(cmd)
        recorded["kwargs"] = kwargs
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    mod._install_from_dir("ansible-galaxy", tmp_path / "src", tmp_path / "dest")
    assert recorded["kwargs"]["timeout"] == GALAXY_TIMEOUT_SECONDS


def test_try_requirements_file_passes_galaxy_timeout(monkeypatch, tmp_path):
    mod = _load_ci_install()
    recorded = {}

    def fake_run(cmd, **kwargs):
        recorded["kwargs"] = kwargs
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.delenv("PLAIBOOK_CI_SKIP_GALAXY", raising=False)
    assert mod.try_requirements_file("ansible-galaxy", tmp_path / "dest") is True
    assert recorded["kwargs"]["timeout"] == GALAXY_TIMEOUT_SECONDS


def test_git_passes_timeout(monkeypatch, tmp_path):
    mod = _load_ci_install()
    recorded = {}

    def fake_run(cmd, **kwargs):
        recorded["kwargs"] = kwargs
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    mod._git(["status"], cwd=tmp_path, token=None)
    assert recorded["kwargs"]["timeout"] == GALAXY_TIMEOUT_SECONDS


def test_required_keys_include_mirror_dependency():
    mod = _load_ci_install()
    keys = mod._required_keys()
    assert ("community", "library_inventory_filtering_v1") in keys
    assert ("ansible", "posix") in keys
