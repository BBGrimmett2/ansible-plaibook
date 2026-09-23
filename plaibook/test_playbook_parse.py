# -*- coding: utf-8 -*-
"""AAP Vertex EEs must parse review.yml without aknochow.cursor installed."""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def test_review_yml_does_not_bind_cursor_bridge_at_parse_time():
    text = (_ROOT / "review.yml").read_text(encoding="utf-8")
    assert "aknochow.cursor.bridge:" not in text
    assert "roles/review/tasks/cursor_sidecar_start.yml" in text
    assert "roles/review/tasks/cursor_sidecar_stop.yml" in text


def test_continuity_audit_loads_cursor_agent_only_via_include():
    text = (_ROOT / "roles/review/tasks/verify_continuity_audit.yml").read_text(encoding="utf-8")
    assert "aknochow.cursor.agent:" not in text
    assert "verify_continuity_audit_cursor.yml" in text
    cursor = (_ROOT / "roles/review/tasks/verify_continuity_audit_cursor.yml").read_text(encoding="utf-8")
    assert "aknochow.cursor.agent:" in cursor
