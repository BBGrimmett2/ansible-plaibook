# -*- coding: utf-8 -*-
"""Keep PR @mentions from looking like git credentials, without hiding secrets.

``credentials-in-git-url`` matches across newlines: any http(s) URL,
the next colon, then a later at-github / at-gitlab / at-bitbucket host.
A github.com PR URL plus a github-advanced-security review comment is
that shape and is not a credential.

Do not insert characters into every newline to break that match: a
credential split across source lines (adjacent string literals, a line
continuation) uses the same spanning, and must still reach the scanner.
Drop unified-diff deletion lines instead, so a removed git userinfo URL
in the patch does not span into a later at-github host. Then strip
whitespace/quote-prefixed @host mentions. Same-line userinfo and
split-across-lines userinfo both still match.
"""

from __future__ import annotations

import re
from typing import Any

# Newline is not a mention prefix: "password\\n@github.com" is a split
# credential, not a review comment.
_HOST_MENTION_RE = re.compile(r"(^|[ \t`\"'(\[])@(github|gitlab|bitbucket|dev\.azure)\b")


def drop_unified_diff_deletions(text: str) -> str:
    """Omit unified-diff deletion lines. File headers (``--- a/file``) stay.

    Call this on the diff only, before concatenating the PR/MR description;
    markdown list items in the description start with ``- `` and must be kept.
    """
    if not isinstance(text, str):
        return text
    return _drop_minus_lines(text)


def _drop_minus_lines(diff: str) -> str:
    out: list[str] = []
    for line in diff.splitlines(keepends=True):
        if line.startswith("-") and not line.startswith("---"):
            continue
        out.append(line)
    return "".join(out)


def neutralize_host_mentions(text: str) -> str:
    if not isinstance(text, str):
        return text
    return _HOST_MENTION_RE.sub(r"\1\2", text)


def prepare_guardian_scan_input(text: str) -> str:
    """Scan-input transforms that do not hide a split git userinfo secret."""
    return neutralize_host_mentions(drop_unified_diff_deletions(text))


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
            "drop_unified_diff_deletions": drop_unified_diff_deletions,
            "neutralize_host_mentions": neutralize_host_mentions,
            "prepare_guardian_scan_input": prepare_guardian_scan_input,
            "blocking_guardian_findings": blocking_guardian_findings,
        }
