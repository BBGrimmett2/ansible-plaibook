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
    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "+print('ok')\n"
    )
    description = (
        "- **URL**: https://github.com/aknochow/ansible-plaibook/pull/64\n"
        "- **CI**: passing\n"
        "\n"
        "`pip install git+https://github.com/aknochow/ansible-plaibook`\n"
        "- CI: Ubuntu 3.10\n"
        "1. @github-advanced-security: scorecard\n"
    )
    scanned = (
        mod.drop_unified_diff_deletions(diff)
        + "\n--- PR/MR description ---\n"
        + description
    )
    assert _GIT_CREDENTIAL_URL.search(diff + "\n--- PR/MR description ---\n" + description)
    cleaned = mod.neutralize_host_mentions(scanned)
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


def test_deleted_git_userinfo_line_is_not_scanned():
    mod = _filter()
    password = "s" + "ecretvalue1"
    text = (
        'clone = "https://github.com/org/repo.git"\n'
        "diff --git a/x.py b/x.py\n"
        "--- a/x.py\n"
        "+++ b/x.py\n"
        f'-    token = "https://x-access-token:{password}@'
        + "github.com/org/repo.git\"\n"
        "+    token = None\n"
    )
    assert _GIT_CREDENTIAL_URL.search(text)
    assert _GIT_CREDENTIAL_URL.search(mod.prepare_guardian_scan_input(text)) is None


def test_same_line_git_userinfo_still_matches_credentials_rule():
    mod = _filter()
    password = "s" + "ecretvalue1"
    url = "https://user:" + password + "@" + "github.com/org/repo.git"
    assert _GIT_CREDENTIAL_URL.search(url)
    cleaned = mod.prepare_guardian_scan_input("clone " + url + "\nnext line\n")
    assert url in cleaned
    assert _GIT_CREDENTIAL_URL.search(cleaned)


def test_split_line_git_userinfo_still_matches_credentials_rule():
    mod = _filter()
    password = "s" + "ecretvalue1"
    text = "https://user:\n" + password + "@" + "github.com/org/repo.git\n"
    assert _GIT_CREDENTIAL_URL.search(text)
    cleaned = mod.prepare_guardian_scan_input(text)
    assert password in cleaned
    assert _GIT_CREDENTIAL_URL.search(cleaned)


def test_added_split_git_userinfo_in_diff_still_matches():
    mod = _filter()
    password = "s" + "ecretvalue1"
    text = (
        "diff --git a/x.py b/x.py\n"
        "--- a/x.py\n"
        "+++ b/x.py\n"
        '+url = ("https://user:"\n'
        f'+       "{password}@' + 'github.com/org/repo.git")\n'
    )
    assert _GIT_CREDENTIAL_URL.search(text)
    assert _GIT_CREDENTIAL_URL.search(mod.prepare_guardian_scan_input(text))


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
    assert secret in cleaned
    assert "@github.com" in cleaned
