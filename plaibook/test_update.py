# -*- coding: utf-8 -*-
"""Unit tests for plaibook update functionality."""

from __future__ import annotations

import json
from unittest.mock import MagicMock
from urllib.error import URLError

import pytest

from plaibook.cli import cmd_update
from plaibook.update import (
    NetworkError,
    UpdateError,
    _validate_github_ref,
    current_version,
    fetch_pypi_latest_version,
    pipx_install_git_ref,
    pipx_install_pypi,
    pipx_installed_version,
    pipx_package_spec,
    pipx_spec_is_pypi,
    prompt_confirm,
    update_lock_path,
)


def test_update_lock_path_default():
    """Test update_lock_path returns correct path."""
    lock_path = update_lock_path()
    assert lock_path.name == "updates.lock"
    assert "ansible-plaibook" in str(lock_path)


def test_update_lock_busy_does_not_say_to_delete_the_file(monkeypatch, tmp_path):
    """The lock releases when the holder exits. Deleting the path does not."""
    import plaibook.update as update

    def busy(*_args, **_kwargs):
        raise OSError(11, "Resource temporarily unavailable")

    monkeypatch.setattr(update.fcntl, "flock", busy)
    monkeypatch.setattr(update, "update_lock_path", lambda home=None: tmp_path / "updates.lock")
    with pytest.raises(UpdateError, match="released when that process exits") as caught:
        with update._exclusive_update_lock():
            pass
    assert "remove" not in str(caught.value)


def test_current_version():
    """Test current_version returns the package version."""
    version = current_version()
    assert isinstance(version, str)
    assert len(version) > 0
    # Should match semantic versioning pattern
    assert "." in version


def test_validate_github_ref_accepts_valid_refs():
    """Test _validate_github_ref accepts valid refs."""
    assert _validate_github_ref("main") == "main"
    assert _validate_github_ref("v0.1.26") == "v0.1.26"
    assert _validate_github_ref("feature/branch-name") == "feature/branch-name"
    sha40 = "abc1234567890123456789012345678901234567"
    assert _validate_github_ref(sha40) == sha40


def test_validate_github_ref_rejects_traversal():
    """Test _validate_github_ref rejects path traversal attempts."""
    with pytest.raises(UpdateError, match="path traversal"):
        _validate_github_ref("../evil")
    with pytest.raises(UpdateError, match="path traversal"):
        _validate_github_ref("branch/../evil")
    with pytest.raises(UpdateError, match="path traversal"):
        _validate_github_ref("/absolute/path")


def test_validate_github_ref_rejects_empty():
    """Test _validate_github_ref rejects empty refs."""
    with pytest.raises(UpdateError, match="cannot be empty"):
        _validate_github_ref("")
    with pytest.raises(UpdateError, match="cannot be empty"):
        _validate_github_ref("   ")


def test_fetch_pypi_latest_version_success(monkeypatch):
    """Test fetch_pypi_latest_version with successful API response."""
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        "info": {"version": "0.1.27"}
    }).encode("utf-8")
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    def mock_urlopen(request, timeout=None):
        return mock_response

    monkeypatch.setattr("plaibook.update.urlopen", mock_urlopen)

    version = fetch_pypi_latest_version()
    assert version == "0.1.27"


def test_fetch_pypi_latest_version_network_error(monkeypatch):
    """Test fetch_pypi_latest_version handles network errors."""
    def mock_urlopen(request, timeout=None):
        raise URLError("Network error")

    monkeypatch.setattr("plaibook.update.urlopen", mock_urlopen)

    with pytest.raises(NetworkError, match="Failed to fetch PyPI metadata"):
        fetch_pypi_latest_version()


def test_fetch_pypi_latest_version_invalid_json(monkeypatch):
    """Test fetch_pypi_latest_version handles invalid JSON."""
    mock_response = MagicMock()
    mock_response.read.return_value = b"not json"
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    def mock_urlopen(request, timeout=None):
        return mock_response

    monkeypatch.setattr("plaibook.update.urlopen", mock_urlopen)

    with pytest.raises(NetworkError, match="invalid JSON"):
        fetch_pypi_latest_version()


def test_fetch_pypi_latest_version_missing_version(monkeypatch):
    """Test fetch_pypi_latest_version handles missing version in response."""
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        "info": {}  # Missing version
    }).encode("utf-8")
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    def mock_urlopen(request, timeout=None):
        return mock_response

    monkeypatch.setattr("plaibook.update.urlopen", mock_urlopen)

    with pytest.raises(NetworkError, match="invalid JSON"):
        fetch_pypi_latest_version()


def test_prompt_confirm_yes(monkeypatch):
    """Test prompt_confirm returns True for 'y' input."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")

    assert prompt_confirm("Test?") is True


def test_prompt_confirm_no(monkeypatch):
    """Test prompt_confirm returns False for 'n' input."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")

    assert prompt_confirm("Test?") is False


def test_prompt_confirm_default_yes(monkeypatch):
    """Test prompt_confirm returns default for empty input."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "")

    assert prompt_confirm("Test?", default=True) is True
    assert prompt_confirm("Test?", default=False) is False


def test_prompt_confirm_not_tty(monkeypatch):
    """A non-interactive prompt fails closed. Pass --yes to proceed."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    assert prompt_confirm("Test?", default=True) is False
    assert prompt_confirm("Test?", default=False) is False


def test_prompt_confirm_keyboard_interrupt(monkeypatch):
    """Test prompt_confirm handles KeyboardInterrupt."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: (_ for _ in ()).throw(KeyboardInterrupt()))

    assert prompt_confirm("Test?") is False


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def test_pipx_install_git_ref_argv(monkeypatch):
    """--branch installs that ref with pipx, which has no --branch flag."""
    captured = []

    def mock_which(name):
        return "/usr/bin/pipx" if name == "pipx" else None

    def mock_run(argv, **kwargs):
        captured.append(argv)
        return _completed()

    monkeypatch.setattr("plaibook.update.shutil.which", mock_which)
    monkeypatch.setattr("subprocess.run", mock_run)

    pipx_install_git_ref("v0.1.26")

    assert captured == [[
        "/usr/bin/pipx",
        "install",
        "--force",
        "--pip-args=--no-cache-dir",
        "git+https://github.com/aknochow/ansible-plaibook.git@v0.1.26",
    ]]


def test_pipx_install_git_ref_rejects_traversal(monkeypatch):
    """A bad ref never reaches pipx."""
    monkeypatch.setattr("plaibook.update.shutil.which", lambda name: "/usr/bin/pipx")

    with pytest.raises(UpdateError, match="path traversal"):
        pipx_install_git_ref("../evil")


def test_pipx_install_pypi_argv(monkeypatch):
    """The default path installs that exact PyPI release."""
    captured = []

    def mock_which(name):
        return "/usr/bin/pipx" if name == "pipx" else None

    def mock_run(argv, **kwargs):
        captured.append(argv)
        return _completed()

    monkeypatch.setattr("plaibook.update.shutil.which", mock_which)
    monkeypatch.setattr("subprocess.run", mock_run)

    pipx_install_pypi("0.1.27")

    assert captured == [[
        "/usr/bin/pipx",
        "install",
        "--force",
        "--pip-args=--no-cache-dir",
        "plaibook==0.1.27",
    ]]


def test_pipx_install_pypi_rejects_bad_version():
    """A version that is not a release name never reaches pipx."""
    with pytest.raises(UpdateError, match="Invalid PyPI version"):
        pipx_install_pypi("1..2")
    with pytest.raises(UpdateError, match="Invalid PyPI version"):
        pipx_install_pypi("plaibook==0.1.27")


def test_pipx_package_spec_reads_a_git_url(monkeypatch):
    """pipx list --json is the recorded source, not this process."""
    payload = {
        "venvs": {
            "plaibook": {
                "metadata": {
                    "main_package": {
                        "package": "plaibook",
                        "package_or_url": "git+https://github.com/aknochow/ansible-plaibook.git@main",
                    }
                }
            }
        }
    }
    monkeypatch.setattr("plaibook.update.shutil.which", lambda name: "/usr/bin/pipx")

    def mock_run(argv, **kwargs):
        assert argv == ["/usr/bin/pipx", "list", "--json"]
        return _completed(stdout=json.dumps(payload))

    monkeypatch.setattr("subprocess.run", mock_run)
    spec = pipx_package_spec()
    assert spec is not None
    assert pipx_spec_is_pypi(spec) is False
    assert pipx_spec_is_pypi("plaibook") is True
    assert pipx_spec_is_pypi("plaibook==0.1.26") is True
    assert pipx_spec_is_pypi(None) is True


def test_pipx_package_spec_missing_venv_is_not_a_git_install(monkeypatch):
    """A successful list with no plaibook venv is not a git spec."""
    monkeypatch.setattr("plaibook.update.shutil.which", lambda name: "/usr/bin/pipx")

    def mock_run(argv, **kwargs):
        return _completed(stdout='{"venvs": {}}')

    monkeypatch.setattr("subprocess.run", mock_run)
    assert pipx_package_spec() is None


def test_pipx_package_spec_rejects_an_unreadable_list(monkeypatch):
    """A failed or malformed list is not treated as a PyPI install."""
    monkeypatch.setattr("plaibook.update.shutil.which", lambda name: "/usr/bin/pipx")

    def failed(argv, **kwargs):
        return _completed(returncode=1, stderr="pipx failed")

    monkeypatch.setattr("subprocess.run", failed)
    with pytest.raises(UpdateError, match="will not treat the install as a PyPI release"):
        pipx_package_spec()

    def malformed(argv, **kwargs):
        return _completed(stdout="not-json")

    monkeypatch.setattr("subprocess.run", malformed)
    with pytest.raises(UpdateError, match="not JSON"):
        pipx_package_spec()


def test_pipx_installed_version_reads_show(monkeypatch):
    """The installed version comes from the pipx venv, not this process."""
    monkeypatch.setattr("plaibook.update.shutil.which", lambda name: "/usr/bin/pipx")

    def mock_run(argv, **kwargs):
        assert argv == ["/usr/bin/pipx", "runpip", "plaibook", "show", "plaibook"]
        return _completed(stdout="Name: plaibook\nVersion: 0.1.27\n")

    monkeypatch.setattr("subprocess.run", mock_run)
    assert pipx_installed_version() == "0.1.27"


def test_pipx_missing(monkeypatch):
    """Install paths fail closed when pipx is not installed."""
    monkeypatch.setattr("plaibook.update.shutil.which", lambda name: None)

    with pytest.raises(UpdateError, match="pipx is not on PATH"):
        pipx_install_git_ref("main")
    with pytest.raises(UpdateError, match="pipx is not on PATH"):
        pipx_install_pypi("0.1.27")


def test_cmd_update_check_does_not_call_pipx(monkeypatch, capsys):
    """--check compares versions and does not upgrade."""
    monkeypatch.setattr("plaibook.cli.current_version", lambda: "0.1.26")
    monkeypatch.setattr("plaibook.cli.fetch_pypi_latest_version", lambda: "0.1.27")
    monkeypatch.setattr(
        "plaibook.cli.pipx_install_pypi",
        lambda version: (_ for _ in ()).throw(AssertionError("pipx should not run")),
    )

    code = cmd_update(argparse_namespace(check=True, branch=None, yes=False))

    assert code == 0
    err = capsys.readouterr().err
    assert "0.1.26" in err
    assert "0.1.27" in err


def test_cmd_update_default_installs_pypi_release(monkeypatch, capsys):
    """The default path installs plaibook==VERSION and checks the pipx venv."""
    calls = []
    monkeypatch.setattr("plaibook.cli.current_version", lambda: "0.1.26")
    monkeypatch.setattr("plaibook.cli.fetch_pypi_latest_version", lambda: "0.1.27")
    monkeypatch.setattr("plaibook.update._exclusive_update_lock", _null_lock)
    monkeypatch.setattr(
        "plaibook.cli.pipx_install_pypi",
        lambda version: calls.append(version) or _completed(stdout="installed"),
    )
    monkeypatch.setattr("plaibook.cli.pipx_installed_version", lambda: "0.1.27")

    code = cmd_update(argparse_namespace(check=False, branch=None, yes=True))

    assert code == 0
    assert calls == ["0.1.27"]
    assert "Successfully updated plaibook to 0.1.27" in capsys.readouterr().err


def test_cmd_update_rejects_a_version_pipx_did_not_install(monkeypatch, capsys):
    """Success is the version in the pipx venv, not the pipx return code."""
    monkeypatch.setattr("plaibook.cli.current_version", lambda: "0.1.26")
    monkeypatch.setattr("plaibook.cli.fetch_pypi_latest_version", lambda: "0.1.27")
    monkeypatch.setattr("plaibook.update._exclusive_update_lock", _null_lock)
    monkeypatch.setattr("plaibook.cli.pipx_install_pypi", lambda version: _completed())
    monkeypatch.setattr("plaibook.cli.pipx_installed_version", lambda: "0.1.26")

    code = cmd_update(argparse_namespace(check=False, branch=None, yes=True))

    assert code == 2
    err = capsys.readouterr().err
    assert "expected 0.1.27" in err
    assert "Successfully" not in err


def test_cmd_update_not_tty_requires_yes(monkeypatch, capsys):
    """Without a terminal, the default path cancels unless --yes is set."""
    monkeypatch.setattr("plaibook.cli.current_version", lambda: "0.1.26")
    monkeypatch.setattr("plaibook.cli.fetch_pypi_latest_version", lambda: "0.1.27")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(
        "plaibook.cli.pipx_install_pypi",
        lambda version: (_ for _ in ()).throw(AssertionError("pipx should not run")),
    )

    code = cmd_update(argparse_namespace(check=False, branch=None, yes=False))

    assert code == 1
    err = capsys.readouterr().err
    assert "--yes" in err
    assert "Update cancelled." in err


def test_cmd_update_branch_calls_pipx_install(monkeypatch):
    """--branch records a new git spec via pipx install --force."""
    calls = []
    monkeypatch.setattr("plaibook.cli.current_version", lambda: "0.1.26")
    monkeypatch.setattr("plaibook.cli.prompt_confirm", lambda message, default=True: True)
    monkeypatch.setattr("plaibook.update._exclusive_update_lock", _null_lock)
    monkeypatch.setattr(
        "plaibook.cli.pipx_install_git_ref",
        lambda ref: calls.append(ref) or _completed(),
    )

    code = cmd_update(argparse_namespace(check=False, branch="main", yes=True))

    assert code == 0
    assert calls == ["main"]


def test_cmd_update_already_current_skips_pipx(monkeypatch):
    """A matching PyPI spec does not call pipx install."""
    monkeypatch.setattr("plaibook.cli.current_version", lambda: "0.1.26")
    monkeypatch.setattr("plaibook.cli.fetch_pypi_latest_version", lambda: "0.1.26")
    monkeypatch.setattr("plaibook.cli.pipx_package_spec", lambda: "plaibook")
    monkeypatch.setattr(
        "plaibook.cli.pipx_install_pypi",
        lambda version: (_ for _ in ()).throw(AssertionError("pipx should not run")),
    )

    code = cmd_update(argparse_namespace(check=False, branch=None, yes=True))

    assert code == 0


def test_cmd_update_refuses_to_skip_when_the_pipx_spec_cannot_be_read(monkeypatch, capsys):
    """A matching version is not 'up to date' when pipx's spec is unreadable."""
    monkeypatch.setattr("plaibook.cli.current_version", lambda: "0.1.26")
    monkeypatch.setattr("plaibook.cli.fetch_pypi_latest_version", lambda: "0.1.26")

    def unreadable():
        raise UpdateError(
            "Could not read plaibook's pipx spec (pipx list --json failed), "
            "so this command will not treat the install as a PyPI release."
        )

    monkeypatch.setattr("plaibook.cli.pipx_package_spec", unreadable)
    monkeypatch.setattr(
        "plaibook.cli.pipx_install_pypi",
        lambda version: (_ for _ in ()).throw(AssertionError("pipx should not run")),
    )

    code = cmd_update(argparse_namespace(check=False, branch=None, yes=True))

    assert code == 2
    err = capsys.readouterr().err
    assert "already up to date" not in err
    assert "will not treat the install as a PyPI release" in err


def test_cmd_update_replaces_a_git_spec_at_the_same_version(monkeypatch, capsys):
    """A branch install does not stick when its version already matches PyPI."""
    calls = []
    monkeypatch.setattr("plaibook.cli.current_version", lambda: "0.1.26")
    monkeypatch.setattr("plaibook.cli.fetch_pypi_latest_version", lambda: "0.1.26")
    monkeypatch.setattr(
        "plaibook.cli.pipx_package_spec",
        lambda: "git+https://github.com/aknochow/ansible-plaibook.git@main",
    )
    monkeypatch.setattr("plaibook.update._exclusive_update_lock", _null_lock)
    monkeypatch.setattr(
        "plaibook.cli.pipx_install_pypi",
        lambda version: calls.append(version) or _completed(),
    )
    monkeypatch.setattr("plaibook.cli.pipx_installed_version", lambda: "0.1.26")

    code = cmd_update(argparse_namespace(check=False, branch=None, yes=True))

    assert code == 0
    assert calls == ["0.1.26"]
    assert "installed from a git ref" in capsys.readouterr().err


def argparse_namespace(**kwargs):
    from argparse import Namespace

    return Namespace(**kwargs)


def _null_lock(home=None):
    from contextlib import nullcontext

    return nullcontext()
