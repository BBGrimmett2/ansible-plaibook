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
    collection_is_installed,
    collection_key_from_requirement,
    collections_dir,
    collections_lock_path,
    ensure_collections,
    merge_collections_path,
    requirement_collection_keys,
)
from plaibook.playbook import run_ansible_playbook


def _plant(dest, *keys):
    for ns, name in keys:
        coll = dest / "ansible_collections" / ns / name
        coll.mkdir(parents=True, exist_ok=True)
        (coll / "MANIFEST.json").write_text("{}\n")


def _install_spies(monkeypatch, calls=None):
    """GitHub-first install without ansible-galaxy -r or the Galaxy API."""
    recorded = calls if calls is not None else []

    def fake_git(galaxy, dest, cols, env):
        recorded.append(("git", list(cols), dict(env)))
        for col in cols:
            key = collection_key_from_requirement(col)
            if key:
                _plant(dest, key)

    def fake_mirrors(galaxy, dest, env, needed=None):
        recorded.append(("mirrors", list(needed or []), dict(env)))
        if needed:
            _plant(dest, *needed)

    monkeypatch.setattr("plaibook.collections.install_git_sources", fake_git)
    monkeypatch.setattr("plaibook.collections.install_galaxy_github_mirrors", fake_mirrors)
    return recorded


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
    recorded = _install_spies(monkeypatch)
    dest = ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")
    assert dest == home / ".cache" / "ansible-plaibook" / "collections"
    assert dest.is_dir()
    assert ".ansible" not in str(dest)
    assert recorded, "expected GitHub-first install on first run"
    env = recorded[0][2]
    assert env["GIT_CONFIG_KEY_0"] == "advice.detachedHead"
    assert env["GIT_CONFIG_VALUE_0"] == "false"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    stamp = dest / ".requirements.sha256"
    assert stamp.is_file()
    assert "ANSIBLE_COLLECTIONS_PATHS" not in env

    recorded.clear()
    again = ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")
    assert again == dest
    assert recorded == []


def test_ensure_collections_drops_legacy_paths_from_galaxy_env(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text("collections: []\n")
    home = tmp_path / "home"
    home.mkdir()
    recorded = _install_spies(monkeypatch)
    monkeypatch.setenv("ANSIBLE_COLLECTIONS_PATHS", "/opt/legacy")
    ensure_collections(
        playbook,
        home=home,
        galaxy_bin="ansible-galaxy",
        env={"ANSIBLE_COLLECTIONS_PATHS": "/from-caller"},
    )
    assert recorded
    galaxy_env = recorded[0][2]
    assert "ANSIBLE_COLLECTIONS_PATHS" not in galaxy_env
    assert galaxy_env["ANSIBLE_COLLECTIONS_PATH"].split(os.pathsep)[0] == str(collections_dir(home))


def test_ensure_collections_reinstalls_when_requirements_change(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    req = playbook / "collections-requirements.yml"
    req.write_text("collections: []\n")
    home = tmp_path / "home"
    dest = collections_dir(home)
    dest.mkdir(parents=True)
    (dest / ".requirements.sha256").write_text("not-the-digest\n")
    recorded = _install_spies(monkeypatch)
    err = []

    class Err:
        def write(self, text):
            err.append(text)

        def flush(self):
            pass

    ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy", stderr=Err())
    assert recorded
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
    recorded = _install_spies(monkeypatch)
    err = []

    class Err:
        def write(self, text):
            err.append(text)

        def flush(self):
            pass

    ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy", stderr=Err())
    assert recorded
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
    recorded = _install_spies(monkeypatch)
    ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")
    assert recorded


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


def test_ensure_collections_reinstalls_when_a_dependency_masks_a_required_name(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    req = playbook / "collections-requirements.yml"
    req.write_text("collections:\n  - name: ansible.posix\n  - name: kubernetes.core\n")
    home = tmp_path / "home"
    dest = collections_dir(home)
    dest.mkdir(parents=True)
    digest = hashlib.sha256(req.read_bytes()).hexdigest()
    (dest / ".requirements.sha256").write_text(digest + "\n")
    # Two installed dirs (count would match) but kubernetes.core is missing;
    # a dependency must not stand in for it.
    for ns, name in (("ansible", "posix"), ("community", "general")):
        coll = dest / "ansible_collections" / ns / name
        coll.mkdir(parents=True)
        (coll / "MANIFEST.json").write_text("{}\n")
    recorded = _install_spies(monkeypatch)
    ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")
    assert recorded


def test_requirement_collection_keys_maps_git_urls_and_fqcns(tmp_path):
    req = tmp_path / "collections-requirements.yml"
    req.write_text(
        "collections:\n"
        "  - name: https://github.com/aknochow/ansible-openshell.git\n"
        "    type: git\n"
        "  - name: community.general\n"
        "  - name: kubernetes.core\n"
        "  - name: ansible.posix\n"
    )
    assert requirement_collection_keys(req) == [
        ("aknochow", "openshell"),
        ("community", "general"),
        ("kubernetes", "core"),
        ("ansible", "posix"),
    ]
    assert collection_key_from_requirement(
        {"name": "https://github.com/ansible-collections/ansible.posix.git", "type": "git"}
    ) == ("ansible", "posix")
    dest = tmp_path / "dest"
    posix = dest / "ansible_collections" / "ansible" / "posix"
    posix.mkdir(parents=True)
    (posix / "MANIFEST.json").write_text("{}\n")
    assert collection_is_installed(dest, "ansible", "posix")
    assert not collection_is_installed(dest, "kubernetes", "core")


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


def test_ensure_collections_surfaces_github_install_failure(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text(
        "collections:\n  - name: https://github.com/example/ansible-posix.git\n    type: git\n"
    )
    home = tmp_path / "home"

    def fake_git(*args, **kwargs):
        raise CollectionInstallError("git fetch failed for example/ansible-posix")

    monkeypatch.setattr("plaibook.collections.install_git_sources", fake_git)
    monkeypatch.setattr("plaibook.collections.install_galaxy_github_mirrors", lambda *a, **k: None)
    with pytest.raises(CollectionInstallError, match="git fetch failed"):
        ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")


def test_ensure_collections_installs_from_github_never_galaxy_api(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text(
        "collections:\n"
        "  - name: https://github.com/ansible-collections/community.general.git\n"
        "    type: git\n"
        "    version: '13.4.0'\n"
    )
    home = tmp_path / "home"
    err = []

    class Err:
        def write(self, text):
            err.append(text)

        def flush(self):
            pass

    def boom_run(cmd, **kwargs):
        joined = " ".join(str(part) for part in cmd)
        raise AssertionError(f"first-run install must not call Galaxy or git here: {joined}")

    recorded = _install_spies(monkeypatch)
    monkeypatch.setattr("plaibook.collections.subprocess.run", boom_run)
    dest = ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy", stderr=Err())
    assert dest == collections_dir(home)
    log = "".join(err)
    assert "Installing Ansible collections from GitHub" in log
    assert "Galaxy install failed" not in log
    assert any(kind == "git" for kind, *_ in recorded)
    assert collection_is_installed(dest, "community", "general")
    assert collection_is_installed(dest, "community", "library_inventory_filtering_v1")
    assert (dest / ".requirements.sha256").is_file()


def test_ensure_collections_installs_git_sources_with_no_deps(tmp_path, monkeypatch):
    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text(
        "collections:\n  - name: https://github.com/example/ansible-posix.git\n    type: git\n    version: HEAD\n"
    )
    home = tmp_path / "home"
    calls = []

    def fake_clone(url, ref, dest):
        dest.mkdir(parents=True, exist_ok=True)

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        assert "-r" not in cmd
        dest = Path(cmd[cmd.index("-p") + 1])
        _plant(dest, ("example", "posix"))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("plaibook.collections._clone_at_ref", fake_clone)
    monkeypatch.setattr("plaibook.collections.subprocess.run", fake_run)
    dest = ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")
    assert dest == collections_dir(home)
    assert calls
    assert all(c[:3] == ["ansible-galaxy", "collection", "install"] for c in calls)
    assert all("--no-deps" in c for c in calls)
    assert collection_is_installed(dest, "example", "posix")


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
    assert "ANSIBLE_COLLECTIONS_PATHS" not in env


def test_merge_collections_path_drops_legacy_paths_var(tmp_path):
    home = tmp_path / "home"
    isolated = str(collections_dir(home))
    env = {"ANSIBLE_COLLECTIONS_PATHS": "/opt/legacy"}
    merge_collections_path(env, home=home)
    assert env["ANSIBLE_COLLECTIONS_PATH"].split(os.pathsep) == [isolated, "/opt/legacy"]
    assert "ANSIBLE_COLLECTIONS_PATHS" not in env


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
    assert env["TMPDIR"] == str(home / ".cache" / "ansible-plaibook" / "tmp")
    scratch = Path(env["TMPDIR"])
    assert scratch.is_dir()
    info = scratch.stat()
    assert info.st_uid == os.geteuid()
    assert (info.st_mode & 0o777) == 0o700
    assert Path(env["ANSIBLE_CONFIG"]) == tmp_path / "ansible.cfg"


def test_run_ansible_playbook_does_not_keep_slash_tmp(tmp_path, monkeypatch):
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
    monkeypatch.setenv("TMPDIR", "/tmp")
    (tmp_path / "ansible.cfg").write_text("[defaults]\n")
    home = tmp_path / "home"
    run_ansible_playbook(
        ["ansible-playbook", "review.yml"],
        playbook_root=tmp_path,
        verbose=False,
        home=home,
    )
    assert recorded["env"]["TMPDIR"] == str(home / ".cache" / "ansible-plaibook" / "tmp")
    assert recorded["env"]["TMPDIR"] != "/tmp"


def test_runtime_tmp_dir_is_private(tmp_path):
    import stat

    from plaibook.playbook import runtime_tmp_dir

    home = tmp_path / "home"
    path = runtime_tmp_dir(home)
    info = path.stat()
    assert stat.S_ISDIR(info.st_mode)
    assert info.st_uid == os.geteuid()
    assert stat.S_IMODE(info.st_mode) == 0o700


def test_runtime_tmp_dir_rejects_symlink(tmp_path):
    from plaibook.playbook import ScratchDirError, last_run_dir, runtime_tmp_dir

    home = tmp_path / "home"
    cache = last_run_dir(home)
    cache.mkdir(parents=True)
    target = tmp_path / "elsewhere"
    target.mkdir()
    (cache / "tmp").symlink_to(target)
    with pytest.raises(ScratchDirError, match="symlink"):
        runtime_tmp_dir(home)


def test_runtime_tmp_dir_fails_closed_when_chmod_raises(tmp_path, monkeypatch):
    from plaibook.playbook import ScratchDirError, runtime_tmp_dir

    home = tmp_path / "home"

    def boom(path, mode):
        raise OSError("chmod not supported")

    monkeypatch.setattr("os.chmod", boom)
    with pytest.raises(ScratchDirError, match="private"):
        runtime_tmp_dir(home)


def test_runtime_tmp_dir_fails_closed_when_mode_stays_open(tmp_path, monkeypatch):
    from plaibook.playbook import ScratchDirError, runtime_tmp_dir

    home = tmp_path / "home"
    real_chmod = os.chmod

    def leave_open(path, mode):
        real_chmod(path, 0o755)

    monkeypatch.setattr("os.chmod", leave_open)
    with pytest.raises(ScratchDirError, match="not private"):
        runtime_tmp_dir(home)


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


def test_collections_lock_path_is_beside_cache_not_inside(tmp_path):
    home = tmp_path / "home"
    dest = collections_dir(home)
    lock = collections_lock_path(home)
    assert lock.parent == dest.parent
    assert lock.name == "collections.lock"
    assert dest.name == "collections"
    assert dest not in lock.parents


def test_ensure_collections_holds_flock_during_galaxy(tmp_path, monkeypatch):
    import subprocess
    import sys

    import plaibook.collections as coll

    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text("collections: []\n")
    home = tmp_path / "home"
    home.mkdir()
    probe_codes = []
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        lock_path = collections_lock_path(home)
        assert lock_path.is_file()
        probe = real_run(
            [
                sys.executable,
                "-c",
                (
                    "import fcntl, os, sys\n"
                    f"fd = os.open({str(lock_path)!r}, os.O_RDWR)\n"
                    "try:\n"
                    "    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
                    "except BlockingIOError:\n"
                    "    sys.exit(2)\n"
                    "else:\n"
                    "    sys.exit(0)\n"
                    "finally:\n"
                    "    os.close(fd)\n"
                ),
            ],
            check=False,
        )
        probe_codes.append(probe.returncode)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def fake_git(galaxy, dest, cols, env):
        coll.subprocess.run(
            [galaxy, "collection", "install", "-p", str(dest), "--force", "--no-deps"],
            env=env,
            capture_output=True,
        )

    monkeypatch.setattr(coll, "install_git_sources", fake_git)
    monkeypatch.setattr(coll, "install_galaxy_github_mirrors", lambda *a, **k: None)
    monkeypatch.setattr(coll.subprocess, "run", fake_run)
    dest = ensure_collections(playbook, home=home, galaxy_bin="ansible-galaxy")
    assert dest == collections_dir(home)
    assert probe_codes == [2], "a sibling process must not take LOCK_EX while galaxy runs"
    assert collections_lock_path(home).parent == dest.parent
    assert not (dest / "collections.lock").exists()


def _ensure_collections_worker(playbook: str, home: str, galaxy: str, result_path: str) -> None:
    dest = ensure_collections(Path(playbook), home=Path(home), galaxy_bin=galaxy)
    Path(result_path).write_text(str(dest))


def test_ensure_collections_serializes_two_processes(tmp_path):
    import multiprocessing
    import subprocess
    import sys
    import time

    src = tmp_path / "example" / "ansible-posix"
    src.mkdir(parents=True)
    (src / "galaxy.yml").write_text(
        "namespace: example\nname: posix\nversion: 1.0.0\nreadme: README.md\nauthors: [test]\n"
    )
    (src / "README.md").write_text("test\n")
    subprocess.run(["git", "init", "-b", "main"], cwd=src, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=src, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init"],
        cwd=src,
        check=True,
        capture_output=True,
    )

    playbook = tmp_path / "playbook"
    playbook.mkdir()
    (playbook / "collections-requirements.yml").write_text(
        f"collections:\n  - name: {src}\n    type: git\n    version: HEAD\n"
    )
    home = tmp_path / "home"
    home.mkdir()
    log = tmp_path / "galaxy.log"
    release = tmp_path / "release"
    fake_galaxy = tmp_path / "ansible-galaxy"
    fake_galaxy.write_text(
        f"#!{sys.executable}\n"
        "import pathlib, sys, time\n"
        f"log = pathlib.Path({str(log)!r})\n"
        f"release = pathlib.Path({str(release)!r})\n"
        "args = sys.argv\n"
        "dest = pathlib.Path(args[args.index('-p') + 1])\n"
        "coll = dest / 'ansible_collections' / 'example' / 'posix'\n"
        "with log.open('a') as fh:\n"
        "    fh.write('start\\n')\n"
        "    fh.flush()\n"
        "deadline = time.time() + 10\n"
        "while time.time() < deadline and not release.exists():\n"
        "    time.sleep(0.05)\n"
        "coll.mkdir(parents=True, exist_ok=True)\n"
        "(coll / 'MANIFEST.json').write_text('{}\\n')\n"
        "with log.open('a') as fh:\n"
        "    fh.write('end\\n')\n"
        "sys.exit(0)\n"
    )
    fake_galaxy.chmod(0o755)

    ctx = multiprocessing.get_context("spawn")
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    p1 = ctx.Process(
        target=_ensure_collections_worker,
        args=(str(playbook), str(home), str(fake_galaxy), str(first)),
    )
    p2 = ctx.Process(
        target=_ensure_collections_worker,
        args=(str(playbook), str(home), str(fake_galaxy), str(second)),
    )
    p1.start()
    deadline = time.time() + 10
    while time.time() < deadline:
        if log.is_file() and "start" in log.read_text(encoding="utf-8"):
            break
        time.sleep(0.05)
    else:
        p1.kill()
        raise AssertionError("first ansible-galaxy did not start")
    p2.start()
    time.sleep(0.4)
    assert log.read_text(encoding="utf-8").count("start") == 1
    release.write_text("go\n", encoding="utf-8")
    p1.join(timeout=10)
    p2.join(timeout=10)
    assert p1.exitcode == 0
    assert p2.exitcode == 0
    dest = collections_dir(home)
    assert first.read_text(encoding="utf-8") == str(dest)
    assert second.read_text(encoding="utf-8") == str(dest)
    assert log.read_text(encoding="utf-8").count("start") == 1
    assert log.read_text(encoding="utf-8").count("end") == 1
    assert (dest / ".requirements.sha256").is_file()
    lock = collections_lock_path(home)
    assert lock.is_file()
    assert lock.parent == dest.parent
