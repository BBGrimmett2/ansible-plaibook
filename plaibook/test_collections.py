# -*- coding: utf-8 -*-
"""Isolated collections cache: never ~/.ansible, stamp-skip, quiet galaxy."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from plaibook.collections import (
    CollectionInstallError,
    collections_dir,
    ensure_collections,
    merge_collections_path,
)
from plaibook.playbook import run_ansible_playbook


def test_ensure_collections_noop_without_requirements(tmp_path):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    home = tmp_path / "home"
    assert ensure_collections(playbook, home=home) is None
    assert not collections_dir(home).exists()


def test_ensure_collections_installs_into_isolated_cache(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text("collections: []\n")
    home = tmp_path / "home"
    home.mkdir()
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((list(cmd), kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("plaibook.collections.subprocess.run", fake_run)
    dest = ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")
    assert dest == home / ".cache" / "ansible-plaibook" / "collections"
    assert dest.is_dir()
    assert calls, "expected ansible-galaxy on first run"
    cmd, kwargs = calls[0]
    assert cmd[:3] == ["ansible-galaxy", "collection", "install"]
    assert cmd[cmd.index("-p") + 1] == str(dest)
    assert ".ansible" not in str(dest)
    assert "--force" in cmd
    env = kwargs["env"]
    assert env["GIT_CONFIG_KEY_0"] == "advice.detachedHead"
    assert env["GIT_CONFIG_VALUE_0"] == "false"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert kwargs["capture_output"] is True
    stamp = dest / ".requirements.sha256"
    assert stamp.is_file()

    calls.clear()
    again = ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")
    assert again == dest
    assert calls == []


def test_ensure_collections_reinstalls_when_requirements_change(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    req = playbook / "collections-requirements.yml"
    req.write_text("collections: []\n")
    home = tmp_path / "home"
    dest = collections_dir(home)
    dest.mkdir(parents=True)
    (dest / ".requirements.sha256").write_text("not-the-digest\n")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("plaibook.collections.subprocess.run", fake_run)
    err = []

    class Err:
        def write(self, text):
            err.append(text)

        def flush(self):
            pass

    ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy", stderr=Err())
    assert calls
    assert "Updating" in "".join(err)


def test_ensure_collections_reinstalls_when_stamp_tree_missing(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    req = playbook / "collections-requirements.yml"
    req.write_text("collections:\n  - name: ansible.posix\n")
    home = tmp_path / "home"
    dest = collections_dir(home)
    dest.mkdir(parents=True)
    digest = hashlib.sha256(req.read_bytes()).hexdigest()
    (dest / ".requirements.sha256").write_text(digest + "\n")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("plaibook.collections.subprocess.run", fake_run)
    err = []

    class Err:
        def write(self, text):
            err.append(text)

        def flush(self):
            pass

    ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy", stderr=Err())
    assert calls
    assert "cache incomplete" in "".join(err)


def test_ensure_collections_reinstalls_when_a_required_collection_is_missing(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    req = playbook / "collections-requirements.yml"
    req.write_text("collections:\n  - name: ansible.posix\n  - name: kubernetes.core\n")
    home = tmp_path / "home"
    dest = collections_dir(home)
    dest.mkdir(parents=True)
    digest = hashlib.sha256(req.read_bytes()).hexdigest()
    (dest / ".requirements.sha256").write_text(digest + "\n")
    one = dest / "ansible_collections" / "ansible" / "posix"
    one.mkdir(parents=True)
    (one / "MANIFEST.json").write_text("{}\n")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("plaibook.collections.subprocess.run", fake_run)
    ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")
    assert calls


def test_ensure_collections_skips_when_all_required_collections_are_present(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    req = playbook / "collections-requirements.yml"
    req.write_text("collections:\n  - name: ansible.posix\n  - name: kubernetes.core\n")
    home = tmp_path / "home"
    dest = collections_dir(home)
    for ns, name in (("ansible", "posix"), ("kubernetes", "core")):
        coll = dest / "ansible_collections" / ns / name
        coll.mkdir(parents=True)
        (coll / "MANIFEST.json").write_text("{}\n")
    digest = hashlib.sha256(req.read_bytes()).hexdigest()
    dest.mkdir(parents=True, exist_ok=True)
    (dest / ".requirements.sha256").write_text(digest + "\n")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("plaibook.collections.subprocess.run", fake_run)
    ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")
    assert calls == []


def test_ensure_collections_refuses_dangling_symlink_dest(tmp_path):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text("collections: []\n")
    home = tmp_path / "home"
    cache = home / ".cache" / "ansible-plaibook"
    cache.mkdir(parents=True)
    (cache / "collections").symlink_to(tmp_path / "missing")
    with pytest.raises(CollectionInstallError, match="symlink"):
        ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")


def test_ensure_collections_refuses_symlink_dest(tmp_path):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text("collections: []\n")
    home = tmp_path / "home"
    cache = home / ".cache" / "ansible-plaibook"
    cache.mkdir(parents=True)
    real = tmp_path / "elsewhere"
    real.mkdir()
    (cache / "collections").symlink_to(real)
    with pytest.raises(CollectionInstallError, match="symlink"):
        ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")


def test_ensure_collections_refuses_regular_file_dest(tmp_path):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text("collections: []\n")
    home = tmp_path / "home"
    cache = home / ".cache" / "ansible-plaibook"
    cache.mkdir(parents=True)
    (cache / "collections").write_text("not a directory\n")
    with pytest.raises(CollectionInstallError, match="not a directory"):
        ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")


def test_ensure_collections_missing_galaxy_bin_is_collection_error(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text("collections: []\n")
    home = tmp_path / "home"

    def boom():
        raise FileNotFoundError("ansible-galaxy")

    monkeypatch.setattr("plaibook.collections.ansible_galaxy_bin", boom)
    with pytest.raises(CollectionInstallError, match="ansible-galaxy not found"):
        ensure_collections(playbook, home=home)


def test_ensure_collections_surfaces_galaxy_failure(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text("collections: []\n")
    home = tmp_path / "home"

    def fake_run(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="rmtree on a symbolic link")

    monkeypatch.setattr("plaibook.collections.subprocess.run", fake_run)
    with pytest.raises(CollectionInstallError, match="rmtree"):
        ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")


def test_merge_collections_path_puts_cache_first(tmp_path):
    home = tmp_path / "home"
    isolated = str(collections_dir(home))
    env = {
        "ANSIBLE_COLLECTIONS_PATH": os.pathsep.join(["/opt/collections", isolated]),
    }
    merge_collections_path(env, home=home)
    parts = env["ANSIBLE_COLLECTIONS_PATH"].split(os.pathsep)
    assert parts[0] == isolated
    assert parts[1] == "/opt/collections"
    assert parts.count(isolated) == 1
    assert env["ANSIBLE_COLLECTIONS_PATHS"] == env["ANSIBLE_COLLECTIONS_PATH"]


def test_run_ansible_playbook_sets_isolated_collections_path(tmp_path, monkeypatch):
    recorded = {}

    class FakeProc:
        pid = 1
        returncode = 0

        def communicate(self, timeout=None):
            return "", ""

    def fake_popen(**kwargs):
        recorded.update(kwargs)
        return FakeProc()

    monkeypatch.setattr("plaibook.playbook.subprocess.Popen", fake_popen)
    (tmp_path / "ansible.cfg").write_text("[defaults]\n")
    home = tmp_path / "home"
    coll = collections_dir(home)
    coll.mkdir(parents=True)
    run_ansible_playbook(
        ["ansible-playbook", "review.yml"],
        playbook_root=tmp_path,
        verbose=False,
        home=home,
    )
    env = recorded["env"]
    assert env["ANSIBLE_COLLECTIONS_PATH"].split(os.pathsep)[0] == str(coll)
    assert Path(env["ANSIBLE_CONFIG"]) == tmp_path / "ansible.cfg"


def test_materialize_playbook_share_copies_playbook_tree(tmp_path):
    from plaibook._setup_share import SHARE_FILES, SHARE_TREES, materialize_playbook_share

    repo = tmp_path / "repo"
    repo.mkdir()
    for name in SHARE_FILES:
        (repo / name).write_text(f"{name}\n")
    for name in SHARE_TREES:
        nested = repo / name / "keep"
        nested.mkdir(parents=True)
        (nested / "ok.txt").write_text("ok\n")
        (nested / "test_not_shipped.py").write_text("no\n")
        junk = repo / name / "__pycache__"
        junk.mkdir()
        (junk / "x.pyc").write_text("no\n")
    dest = tmp_path / "share"
    result = materialize_playbook_share(repo, dest)
    assert result == dest.resolve()
    for name in SHARE_FILES:
        assert (dest / name).read_text() == f"{name}\n"
    assert (dest / "roles" / "keep" / "ok.txt").is_file()
    assert not (dest / "roles" / "__pycache__").exists()
    assert not (dest / "roles" / "keep" / "test_not_shipped.py").exists()
