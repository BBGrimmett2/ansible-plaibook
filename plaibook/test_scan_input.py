# -*- coding: utf-8 -*-
"""@host mentions in review context must not trip credentials-in-git-url."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

# ai-guardian 1.15.0 secrets.toml rule id credentials-in-git-url.
_GIT_CREDENTIAL_URL = re.compile(
    r"https?://[^:]+:(?!(?:PASSWORD|TOKEN|YOUR_TOKEN|xxx+|X{8,}|\$\{?\w+\}?|%\w+%)@)"
    r"[^@]{8,}@(github|gitlab|bitbucket|dev\.azure)"
)


def _filter():
    path = Path(__file__).resolve().parents[1] / "filter_plugins" / "scan_input.py"
    spec = importlib.util.spec_from_file_location("scan_input", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_pr_url_plus_github_mention_is_not_a_git_credential():
    mod = _filter()
    text = (
        "- **URL**: https://github.com/aknochow/ansible-plaibook/pull/64\n"
        "- **CI**: passing\n"
        "\n"
        "`pip install git+https://github.com/aknochow/ansible-plaibook`\n"
        "- CI: Ubuntu 3.10\n"
        "1. @github-advanced-security: scorecard\n"
    )
    assert _GIT_CREDENTIAL_URL.search(text)
    cleaned = mod.prepare_guardian_scan_input(text)
    assert "github-advanced-security" in cleaned
    assert _GIT_CREDENTIAL_URL.search(cleaned) is None


def test_backtick_host_mention_is_not_a_git_credential():
    mod = _filter()
    text = (
        "any ``https://`` URL, the next colon, then a later ``@github`` mention.\n"
        "PR metadata (``https://github.com/org/repo/pull/1``) plus ``@github-advanced-security``.\n"
    )
    assert _GIT_CREDENTIAL_URL.search(text)
    assert _GIT_CREDENTIAL_URL.search(mod.prepare_guardian_scan_input(text)) is None


def test_clone_url_plus_later_github_host_is_not_a_git_credential():
    mod = _filter()
    spanning = (
        "https://github.com/aknochow/ansible-plaibook.git@abc123\n"
        'url = "https://example.org/path"\n'
        "git@" + "github.com:org/repo.git\n"
    )
    assert _GIT_CREDENTIAL_URL.search(spanning)
    assert _GIT_CREDENTIAL_URL.search(mod.prepare_guardian_scan_input(spanning)) is None


def test_same_line_git_userinfo_still_matches_credentials_rule():
    mod = _filter()
    password = "s" + "ecretvalue1"
    url = "https://user:" + password + "@" + "github.com/org/repo.git"
    assert _GIT_CREDENTIAL_URL.search(url)
    cleaned = mod.prepare_guardian_scan_input("clone " + url + "\nnext line\n")
    assert url in cleaned.replace("\n@ ", "\n")
    assert _GIT_CREDENTIAL_URL.search(cleaned)


def test_blocking_guardian_findings_keeps_credentials_in_git_url():
    mod = _filter()
    findings = [
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: Credentials In Git Url",
            "file_path": "ansible.xxx-ai-guardian-input.txt",
        },
        {
            "rule_id": "SECRET-001",
            "message": "Secret detected: GitHub Personal Access Token",
            "file_path": "config.py",
        },
        {"rule_id": "PROMPT-INJECTION-001", "message": "Prompt injection detected"},
    ]
    blocking = mod.blocking_guardian_findings(findings, ["SECRET-001"])
    assert [item["message"] for item in blocking] == [
        "Secret detected: Credentials In Git Url",
        "Secret detected: GitHub Personal Access Token",
    ]


def test_placeholder_userinfo_is_preserved():
    mod = _filter()
    # PASSWORD is an ai-guardian placeholder; host is concatenated so this
    # file is not SECRET-001 bait even if the lookahead is ignored.
    secret = "https://user:PASSWORD@" + "github.com/org/repo.git"
    cleaned = mod.prepare_guardian_scan_input(f"clone {secret}\n")
    assert secret in cleaned.replace("\n@ ", "\n")
    assert "@github.com" in cleaned
