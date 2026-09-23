# -*- coding: utf-8 -*-
"""Keep PR @mentions from looking like git credentials, without hiding secrets.

``credentials-in-git-url`` matches across newlines: any http(s) URL,
the next colon, then a later at-github / at-gitlab / at-bitbucket host.
A github.com PR URL plus a github-advanced-security review comment is
that shape and is not a credential.

Do not insert characters into every newline to break that match: a
credential split across source lines (adjacent string literals, a line
continuation) uses the same spanning, and must still reach the scanner.
Do not drop unified-diff deletion lines: a credential added then
deleted in the same PR stays in git history and must still force
SECRET-001, including a deleted source line that starts with ``--``
(that becomes ``---`` in the patch and is not a ``--- a/file`` header).
Strip whitespace/quote-prefixed @host mentions only. Same-line userinfo
and split-across-lines userinfo both still match.
"""

from __future__ import annotations

import re
from typing import Any

# Newline is not a mention prefix: "password\\n@github.com" is a split
# credential, not a review comment.
_HOST_MENTION_RE = re.compile(r"(^|[ \t`\"'(\[])@(github|gitlab|bitbucket|dev\.azure)\b")


def neutralize_host_mentions(text: str) -> str:
    if not isinstance(text, str):
        return text
    return _HOST_MENTION_RE.sub(r"\1\2", text)


def prepare_guardian_scan_input(text: str) -> str:
    """Scan-input transforms that do not hide a split git userinfo secret."""
    return neutralize_host_mentions(text)


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
            "prepare_guardian_scan_input": prepare_guardian_scan_input,
            "blocking_guardian_findings": blocking_guardian_findings,
        }
