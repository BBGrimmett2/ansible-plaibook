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
    cleaned = mod.neutralize_host_mentions(text)
    assert "@github" not in cleaned
    assert "github-advanced-security" in cleaned
    assert _GIT_CREDENTIAL_URL.search(cleaned) is None


def test_real_git_userinfo_is_preserved():
    mod = _filter()
    # PASSWORD is an ai-guardian placeholder so this file is not SECRET-001 bait.
    secret = "https://user:PASSWORD@github.com/org/repo.git"
    cleaned = mod.neutralize_host_mentions(f"clone {secret}\n")
    assert secret in cleaned
    assert "@github.com" in cleaned
