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
    _cleartext_http,
    _validate_github_ref,
    current_version,
    fetch_pypi_latest_version,
    pip_install_git_ref,
    pipx_install_git_ref,
    pipx_install_pypi,
    pipx_installed_version,
    pipx_upgrade_plaibook,
    prompt_confirm,
    update_cache_dir,
    update_lock_path,
    validate_pyproject_is_plaibook,
)


def test_update_cache_dir_default():
    """Test update_cache_dir returns correct path."""
    cache_dir = update_cache_dir()
    assert cache_dir.name == "updates"
    assert "ansible-plaibook" in str(cache_dir)


def test_update_cache_dir_custom_home(tmp_path):
    """Test update_cache_dir with custom home."""
    cache_dir = update_cache_dir(home=tmp_path)
    assert cache_dir == tmp_path / ".cache" / "ansible-plaibook" / "updates"


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


def test_cleartext_http_detects_http():
    """Test _cleartext_http detects HTTP URLs."""
    assert _cleartext_http("http://example.com") is True
    assert _cleartext_http("HTTP://example.com") is True
    assert _cleartext_http("  http://example.com  ") is True


def test_cleartext_http_allows_https():
    """Test _cleartext_http allows HTTPS URLs."""
    assert _cleartext_http("https://example.com") is False
    assert _cleartext_http("HTTPS://example.com") is False


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


def test_validate_pyproject_is_plaibook_success(tmp_path):
    """Test validate_pyproject_is_plaibook with valid pyproject.toml."""
    pyproject_content = """
[project]
name = "plaibook"
version = "0.1.27"
description = "Test"
"""
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text(pyproject_content)

    version = validate_pyproject_is_plaibook(tmp_path)
    assert version == "0.1.27"


def test_validate_pyproject_is_plaibook_wrong_name(tmp_path):
    """Test validate_pyproject_is_plaibook rejects wrong project name."""
    pyproject_content = """
[project]
name = "evil-package"
version = "0.1.0"
"""
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text(pyproject_content)

    with pytest.raises(UpdateError, match="Not a plaibook repository"):
        validate_pyproject_is_plaibook(tmp_path)


def test_validate_pyproject_is_plaibook_missing_file(tmp_path):
    """Test validate_pyproject_is_plaibook with missing pyproject.toml."""
    with pytest.raises(UpdateError, match="No pyproject.toml found"):
        validate_pyproject_is_plaibook(tmp_path)


def test_validate_pyproject_is_plaibook_missing_version(tmp_path):
    """Test validate_pyproject_is_plaibook with missing version."""
    pyproject_content = """
[project]
name = "plaibook"
description = "Test"
"""
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text(pyproject_content)

    with pytest.raises(UpdateError, match="No version found"):
        validate_pyproject_is_plaibook(tmp_path)


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


def test_verify_installation_no_expected_version(monkeypatch):
    """Test verify_installation without expected version."""
    def mock_version(package):
        return "0.1.27"

    monkeypatch.setattr("importlib.metadata.version", mock_version)

    # Import after monkeypatch
    from plaibook.update import verify_installation
    assert verify_installation() is True


def test_verify_installation_matching_version(monkeypatch):
    """Test verify_installation with matching version."""
    def mock_version(package):
        return "0.1.27"

    monkeypatch.setattr("importlib.metadata.version", mock_version)

    from plaibook.update import verify_installation
    assert verify_installation("0.1.27") is True


def test_verify_installation_mismatched_version(monkeypatch):
    """Test verify_installation with mismatched version."""
    def mock_version(package):
        return "0.1.26"

    monkeypatch.setattr("importlib.metadata.version", mock_version)

    from plaibook.update import verify_installation
    assert verify_installation("0.1.27") is False


def test_verify_installation_import_error(monkeypatch):
    """Test verify_installation handles import errors."""
    def mock_version(package):
        raise ImportError("Package not found")

    monkeypatch.setattr("importlib.metadata.version", mock_version)

    from plaibook.update import verify_installation
    assert verify_installation() is False


def test_pip_install_git_ref_constructs_correct_url(monkeypatch):
    """Test pip_install_git_ref builds the correct git+https URL."""
    ref = "main"
    captured_argv = []

    def mock_run(argv, **kwargs):
        captured_argv.append(argv)
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        return result

    monkeypatch.setattr("subprocess.run", mock_run)

    pip_install_git_ref(ref)

    assert len(captured_argv) == 1
    argv = captured_argv[0]
    assert "pip" in argv
    assert "install" in argv
    assert "--force-reinstall" in argv
    assert "--no-cache-dir" in argv
    assert "git+https://github.com/aknochow/ansible-plaibook.git@main" in argv


def test_pip_install_git_ref_validates_ref(monkeypatch):
    """Test pip_install_git_ref validates the ref."""
    def mock_run(argv, **kwargs):
        result = MagicMock()
        result.returncode = 0
        return result

    monkeypatch.setattr("subprocess.run", mock_run)

    # Should reject path traversal
    with pytest.raises(UpdateError, match="path traversal"):
        pip_install_git_ref("../evil")


def test_pip_install_git_ref_handles_subprocess_error(monkeypatch):
    """Test pip_install_git_ref handles subprocess errors."""
    import subprocess

    def mock_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, timeout=600)

    monkeypatch.setattr("subprocess.run", mock_run)

    with pytest.raises(UpdateError, match="timed out"):
        pip_install_git_ref("main")


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def test_pipx_upgrade_argv(monkeypatch):
    """pipx upgrade follows the spec pipx already recorded."""
    captured = []

    def mock_which(name):
        return "/usr/bin/pipx" if name == "pipx" else None

    def mock_run(argv, **kwargs):
        captured.append(argv)
        return _completed()

    monkeypatch.setattr("plaibook.update.shutil.which", mock_which)
    monkeypatch.setattr("subprocess.run", mock_run)

    pipx_upgrade_plaibook()

    assert captured == [["/usr/bin/pipx", "upgrade", "plaibook"]]


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

    assert captured == [["/usr/bin/pipx", "install", "--force", "plaibook==0.1.27"]]


def test_pipx_install_pypi_rejects_bad_version():
    """A version that is not a release name never reaches pipx."""
    with pytest.raises(UpdateError, match="Invalid PyPI version"):
        pipx_install_pypi("1..2")
    with pytest.raises(UpdateError, match="Invalid PyPI version"):
        pipx_install_pypi("plaibook==0.1.27")


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
        pipx_upgrade_plaibook()
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
    """A matching PyPI version does not call pipx."""
    monkeypatch.setattr("plaibook.cli.current_version", lambda: "0.1.26")
    monkeypatch.setattr("plaibook.cli.fetch_pypi_latest_version", lambda: "0.1.26")
    monkeypatch.setattr(
        "plaibook.cli.pipx_install_pypi",
        lambda version: (_ for _ in ()).throw(AssertionError("pipx should not run")),
    )

    code = cmd_update(argparse_namespace(check=False, branch=None, yes=True))

    assert code == 0


def argparse_namespace(**kwargs):
    from argparse import Namespace

    return Namespace(**kwargs)


def _null_lock(home=None):
    from contextlib import nullcontext

    return nullcontext()
