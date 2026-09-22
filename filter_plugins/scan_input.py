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

_HOST_MENTION_RE = re.compile(r"(^|\s)@(github|gitlab|bitbucket|dev\.azure)\b")


def neutralize_host_mentions(text: str) -> str:
    if not isinstance(text, str):
        return text
    return _HOST_MENTION_RE.sub(r"\1\2", text)


class FilterModule:
    def filters(self):
        return {"neutralize_host_mentions": neutralize_host_mentions}
