# -*- coding: utf-8 -*-
"""Keep PR @mentions and multiline URL pairs from looking like git credentials.

``credentials-in-git-url`` matches across newlines: any http(s) URL,
the next colon, then a later at-github / at-gitlab / at-bitbucket host.
A github.com PR URL plus a github-advanced-security review comment is
that shape and is not a credential. Same-line userinfo (a password
between colon and at-github.com on one line) is a real SECRET-001 and
must still reach the scanner.
"""

from __future__ import annotations

import re
from typing import Any

_HOST_MENTION_RE = re.compile(r"(^|[\s`\"'(\[])@(github|gitlab|bitbucket|dev\.azure)\b")
# credentials-in-git-url's [^@]{8,} spans lines. Inserting "@ " after each
# newline makes the first @ after a colon the line prefix, not at-github, so
# a clone URL on line 1 plus an at-github host on line 3 no longer matches.
# Same-line https userinfo (password then at-host) is unchanged.
_MULTILINE_BREAK = "\n@ "


def neutralize_host_mentions(text: str) -> str:
    if not isinstance(text, str):
        return text
    return _HOST_MENTION_RE.sub(r"\1\2", text)


def interrupt_multiline_git_credentials(text: str) -> str:
    if not isinstance(text, str):
        return text
    return text.replace("\n", _MULTILINE_BREAK)


def prepare_guardian_scan_input(text: str) -> str:
    """Scan-input transforms that do not hide a same-line git userinfo secret."""
    return interrupt_multiline_git_credentials(neutralize_host_mentions(text))


def blocking_guardian_findings(
    findings: Any,
    rule_ids: Any,
) -> list[dict]:
    """Findings whose rule_id is configured to force NEEDS_CHANGES."""
    ids = {str(item) for item in (rule_ids or []) if item}
    out: list[dict] = []
    if not isinstance(findings, list):
        return out
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        if str(finding.get("rule_id") or "") not in ids:
            continue
        out.append(finding)
    return out


class FilterModule:
    def filters(self):
        return {
            "neutralize_host_mentions": neutralize_host_mentions,
            "interrupt_multiline_git_credentials": interrupt_multiline_git_credentials,
            "prepare_guardian_scan_input": prepare_guardian_scan_input,
            "blocking_guardian_findings": blocking_guardian_findings,
        }
