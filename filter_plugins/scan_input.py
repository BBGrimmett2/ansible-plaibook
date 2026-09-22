# -*- coding: utf-8 -*-
"""Keep PR @mentions from looking like git credentials to ai-guardian.

``credentials-in-git-url`` matches across newlines: any ``https://`` URL,
the next colon, then a later ``@github`` / ``@gitlab`` / ``@bitbucket``
mention. PR metadata (``https://github.com/.../pull/N``) plus an
``@github-advanced-security`` review comment is that shape and is not a
credential. Real userinfo (``https://user:PASSWORD@github.com``) stays,
because that ``@`` is not preceded by whitespace.
"""

from __future__ import annotations

import re
from typing import Any

_HOST_MENTION_RE = re.compile(r"(^|[\s`\"'(\[])@(github|gitlab|bitbucket|dev\.azure)\b")
_CREDENTIALS_IN_GIT_URL_RE = re.compile(r"(?i)credentials\s+in\s+git\s+url")


def neutralize_host_mentions(text: str) -> str:
    if not isinstance(text, str):
        return text
    return _HOST_MENTION_RE.sub(r"\1\2", text)


def blocking_guardian_findings(
    findings: Any,
    rule_ids: Any,
) -> list[dict]:
    """SECRET-001 findings that should force NEEDS_CHANGES.

    ``credentials-in-git-url`` matches ordinary clone URLs plus a later
    ``@github`` in concatenated diff/PR text. Same non-blocking class as
    PROMPT-INJECTION-001: surface it, do not override the verdict.
    """
    ids = {str(item) for item in (rule_ids or []) if item}
    out: list[dict] = []
    if not isinstance(findings, list):
        return out
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        if str(finding.get("rule_id") or "") not in ids:
            continue
        message = str(finding.get("message") or "")
        if _CREDENTIALS_IN_GIT_URL_RE.search(message):
            continue
        out.append(finding)
    return out


class FilterModule:
    def filters(self):
        return {
            "neutralize_host_mentions": neutralize_host_mentions,
            "blocking_guardian_findings": blocking_guardian_findings,
        }
