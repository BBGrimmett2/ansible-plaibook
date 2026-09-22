# -*- coding: utf-8 -*-
"""OpenShell SDK pin, and the Python 3.11 runtime used when plai is 3.10."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from plaibook.openshell_sdk import (
    SDK_SPEC,
    OpenshellSdkError,
    ensure_openshell_sdk,
    find_sdk_python,
    prepare_sandbox_runtime,
    reexec_sandbox_runtime,
    spec_from_direct_url,
    version_satisfies,
)


def _capable(monkeypatch):
    monkeypatch.setattr("plaibook.openshell_sdk.interpreter_supports_sdk", lambda _exe: True)


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
    _capable(monkeypatch)
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

    _capable(monkeypatch)
    monkeypatch.setattr("plaibook.openshell_sdk.sdk_satisfies", fake_satisfies)
    monkeypatch.setattr("plaibook.openshell_sdk._package_present", lambda _exe: True)
    monkeypatch.setattr("plaibook.openshell_sdk.subprocess.run", fake_run)
    ensure_openshell_sdk(stderr=None)
    assert recorded[0][1:5] == ["-m", "pip", "install", "--dry-run"]
    assert recorded[1][1:4] == ["-m", "pip", "uninstall"]
    assert recorded[2][1:4] == ["-m", "pip", "install"]
    assert "--dry-run" not in recorded[2]
    assert recorded[2][-1] == SDK_SPEC


def test_ensure_openshell_sdk_dry_run_failure_does_not_uninstall(monkeypatch):
    recorded = []
    _capable(monkeypatch)
    monkeypatch.setattr("plaibook.openshell_sdk.sdk_satisfies", lambda: False)
    monkeypatch.setattr("plaibook.openshell_sdk._package_present", lambda _exe: True)

    def fake_run(cmd, **kwargs):
        recorded.append(list(cmd))
        if "--dry-run" in cmd:
            return SimpleNamespace(returncode=1, stdout="", stderr="no matching distribution")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("plaibook.openshell_sdk.subprocess.run", fake_run)
    with pytest.raises(OpenshellSdkError, match="no matching distribution"):
        ensure_openshell_sdk()
    assert all("uninstall" not in cmd for cmd in recorded)


def test_ensure_installs_without_uninstall_when_package_absent(monkeypatch):
    recorded = []
    checks = {"n": 0}

    def fake_satisfies():
        checks["n"] += 1
        return checks["n"] > 1

    def fake_run(cmd, **kwargs):
        recorded.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    _capable(monkeypatch)
    monkeypatch.setattr("plaibook.openshell_sdk.sdk_satisfies", fake_satisfies)
    monkeypatch.setattr("plaibook.openshell_sdk._package_present", lambda _exe: False)
    monkeypatch.setattr("plaibook.openshell_sdk.subprocess.run", fake_run)
    ensure_openshell_sdk(stderr=None)
    assert recorded[0][1:4] == ["-m", "pip", "install"]
    assert "--dry-run" not in recorded[0]
    assert all("uninstall" not in cmd for cmd in recorded)


def test_ensure_refuses_python_310_without_calling_pip(monkeypatch):
    calls = []
    monkeypatch.setattr("plaibook.openshell_sdk.interpreter_supports_sdk", lambda _exe: False)
    monkeypatch.setattr("plaibook.openshell_sdk.interpreter_version", lambda _exe: (3, 10, 11))
    monkeypatch.setattr("plaibook.openshell_sdk.subprocess.run", lambda *a, **k: calls.append(a))
    with pytest.raises(OpenshellSdkError, match="3.11"):
        ensure_openshell_sdk()
    assert calls == []


def test_spec_from_direct_url_pins_git_commit():
    spec = spec_from_direct_url(
        {
            "url": "https://github.com/aknochow/ansible-plaibook.git",
            "vcs_info": {"vcs": "git", "commit_id": "abc123", "requested_revision": "some-branch"},
        },
        "0.1.0",
    )
    assert spec == "git+https://github.com/aknochow/ansible-plaibook.git@abc123"


def test_spec_from_direct_url_file_and_pypi():
    assert spec_from_direct_url({"url": "file:///tmp/plaibook"}, "0.1.0") == "/tmp/plaibook"
    assert spec_from_direct_url({"url": "https://files.pythonhosted.org/plaibook.whl"}, "0.1.0") == "plaibook==0.1.0"


def test_find_sdk_python_prefers_311(monkeypatch, tmp_path):
    py311 = tmp_path / "python3.11"
    py312 = tmp_path / "python3.12"
    py311.write_text("")
    py312.write_text("")

    def which(name):
        if name == "python3.11":
            return str(py311)
        if name == "python3.12":
            return str(py312)
        return None

    monkeypatch.setattr("plaibook.openshell_sdk.shutil.which", which)
    monkeypatch.setattr("plaibook.openshell_sdk._BREW_BIN", ())
    monkeypatch.setattr("plaibook.openshell_sdk.interpreter_supports_sdk", lambda _exe: True)
    assert find_sdk_python() == str(py311)


def test_find_sdk_python_uses_homebrew_when_path_misses(monkeypatch, tmp_path):
    brew = tmp_path / "python3.11"
    brew.write_text("")
    monkeypatch.setattr("plaibook.openshell_sdk.shutil.which", lambda _name: None)
    monkeypatch.setattr("plaibook.openshell_sdk._BREW_BIN", (tmp_path,))
    monkeypatch.setattr("plaibook.openshell_sdk.interpreter_supports_sdk", lambda _exe: True)
    assert find_sdk_python() == str(brew)


def test_prepare_runtime_creates_venv_and_installs(monkeypatch, tmp_path):
    recorded = []
    base = tmp_path / "pythons" / "python3.11"
    base.parent.mkdir()
    base.write_text("")

    def fake_run(cmd, **kwargs):
        recorded.append(list(cmd))
        if cmd[1:3] == ["-m", "venv"]:
            bindir = Path(cmd[-1]) / "bin"
            bindir.mkdir(parents=True)
            (bindir / "python").write_text("")
            (bindir / "plai").write_text("")
        return SimpleNamespace(returncode=0, stdout="3.11.11\n", stderr="")

    ensured = []
    monkeypatch.setattr("plaibook.openshell_sdk.find_sdk_python", lambda: str(base))
    monkeypatch.setattr("plaibook.openshell_sdk.plaibook_install_spec", lambda: "git+https://example/plaibook.git@abc")
    monkeypatch.setattr("plaibook.openshell_sdk.interpreter_version", lambda _exe: (3, 11, 11))
    monkeypatch.setattr("plaibook.openshell_sdk.subprocess.run", fake_run)
    monkeypatch.setattr(
        "plaibook.openshell_sdk.ensure_openshell_sdk",
        lambda python, **kwargs: ensured.append(python),
    )
    python = prepare_sandbox_runtime(stderr=None, home=tmp_path)
    runtime = tmp_path / ".cache" / "ansible-plaibook" / "sandbox-runtime"
    assert python == str(runtime / "bin" / "python")
    assert recorded[0][:3] == [str(base), "-m", "venv"]
    assert recorded[1][1:4] == ["-m", "pip", "install"]
    assert recorded[1][-2:] == ["git+https://example/plaibook.git@abc", SDK_SPEC]
    assert ensured == [python]
    stamp = json.loads((tmp_path / ".cache" / "ansible-plaibook" / "sandbox-runtime.json").read_text())
    assert stamp["spec"] == "git+https://example/plaibook.git@abc"


def test_prepare_runtime_reuses_matching_stamp(monkeypatch, tmp_path):
    cache = tmp_path / ".cache" / "ansible-plaibook"
    runtime = cache / "sandbox-runtime"
    bindir = runtime / "bin"
    bindir.mkdir(parents=True)
    (bindir / "python").write_text("")
    (bindir / "plai").write_text("")
    base = tmp_path / "python3.11"
    base.write_text("")
    spec = "git+https://example/plaibook.git@abc"
    (cache / "sandbox-runtime.json").write_text(
        json.dumps({"base": str(base.resolve()), "base_version": "3.11.11", "spec": spec}) + "\n"
    )
    calls = []
    monkeypatch.setattr("plaibook.openshell_sdk.find_sdk_python", lambda: str(base))
    monkeypatch.setattr("plaibook.openshell_sdk.plaibook_install_spec", lambda: spec)
    monkeypatch.setattr("plaibook.openshell_sdk.interpreter_version", lambda _exe: (3, 11, 11))
    monkeypatch.setattr("plaibook.openshell_sdk.subprocess.run", lambda *a, **k: calls.append(a))
    monkeypatch.setattr("plaibook.openshell_sdk.ensure_openshell_sdk", lambda python, **kwargs: None)
    python = prepare_sandbox_runtime(stderr=None, home=tmp_path)
    assert python == str(bindir / "python")
    assert calls == []


def test_prepare_runtime_refuses_symlink(monkeypatch, tmp_path):
    cache = tmp_path / ".cache" / "ansible-plaibook"
    cache.mkdir(parents=True)
    (cache / "sandbox-runtime").symlink_to(tmp_path)
    monkeypatch.setattr("plaibook.openshell_sdk.find_sdk_python", lambda: "/usr/bin/python3.11")
    monkeypatch.setattr("plaibook.openshell_sdk.plaibook_install_spec", lambda: "plaibook==0.1.0")
    with pytest.raises(OpenshellSdkError, match="symlink"):
        prepare_sandbox_runtime(stderr=None, home=tmp_path)


def test_prepare_runtime_errors_when_no_python311(monkeypatch, tmp_path):
    monkeypatch.setattr("plaibook.openshell_sdk.find_sdk_python", lambda: None)
    with pytest.raises(OpenshellSdkError, match="3.11"):
        prepare_sandbox_runtime(stderr=None, home=tmp_path)


def test_reexec_stays_on_capable_interpreter(monkeypatch):
    called = []
    monkeypatch.setattr("plaibook.openshell_sdk.interpreter_supports_sdk", lambda _exe: True)
    monkeypatch.setattr("plaibook.openshell_sdk.ensure_openshell_sdk", lambda **kwargs: called.append(True))

    def fail_exec(*_args, **_kwargs):
        raise AssertionError("exec")

    monkeypatch.setattr("plaibook.openshell_sdk.os.execv", fail_exec)
    reexec_sandbox_runtime(stderr=None)
    assert called == [True]


def test_reexec_switches_to_runtime_plai(monkeypatch, tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    python = bindir / "python"
    plai = bindir / "plai"
    python.write_text("")
    plai.write_text("")
    monkeypatch.setattr("plaibook.openshell_sdk.interpreter_supports_sdk", lambda _exe: False)
    monkeypatch.setattr("plaibook.openshell_sdk.prepare_sandbox_runtime", lambda **kwargs: str(python))
    monkeypatch.setenv("PYTHONPATH", "/opt/homebrew/lib/python3.10/site-packages")
    monkeypatch.setenv("VIRTUAL_ENV", "/tmp/other-venv")
    execed = {}

    def fake_exec(path, argv):
        execed["path"] = path
        execed["argv"] = list(argv)

    monkeypatch.setattr("plaibook.openshell_sdk.os.execv", fake_exec)
    monkeypatch.setattr("plaibook.openshell_sdk.sys.argv", ["plai", "review", "org/repo/1"])
    reexec_sandbox_runtime(stderr=None)
    assert execed["path"] == str(plai)
    assert execed["argv"] == [str(plai), "review", "org/repo/1"]
    assert "PYTHONPATH" not in os.environ
    assert os.environ["VIRTUAL_ENV"] == str(tmp_path.resolve())


def test_reexec_refuses_a_second_switch(monkeypatch):
    monkeypatch.setattr("plaibook.openshell_sdk.interpreter_supports_sdk", lambda _exe: False)
    monkeypatch.setenv("PLAIBOOK_SANDBOX_RUNTIME", "1")

    def fail_prepare(**_kwargs):
        raise AssertionError("prepare")

    monkeypatch.setattr("plaibook.openshell_sdk.prepare_sandbox_runtime", fail_prepare)
    with pytest.raises(OpenshellSdkError, match="twice"):
        reexec_sandbox_runtime(stderr=None)
