# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from plaibook.cursor_http2_proxy import (
    ORIGINAL_CONNECT,
    CursorHttp2ProxyError,
    http2_proxy_mark,
    patch_installed_cursor_sdk,
    patch_session_manager,
)

_ESM_STUB = """\
import * as http2 from "http2";
import { connectErrorFromNodeReason } from "./node-error.js";
function connect(authority, http2SessionOptions) {
    let resolve;
    let reject;
    const conn = new Promise((res, rej) => {
        resolve = res;
        reject = rej;
    });
    const newConn = http2.connect(authority, http2SessionOptions);
    newConn.on("connect", onConnect);
    newConn.on("error", onError);
    function onConnect() {
        resolve === null || resolve === void 0 ? void 0 : resolve(newConn);
        cleanup();
    }
    function onError(err) {
        reject === null || reject === void 0 ? void 0 : reject(connectErrorFromNodeReason(err));
        cleanup();
    }
    function cleanup() {
        newConn.off("connect", onConnect);
        newConn.off("error", onError);
    }
    return {
        t: "connecting",
        conn,
        abort(reason) {
            if (!newConn.destroyed) {
                newConn.destroy(undefined, http2.constants.NGHTTP2_CANCEL);
            }
            reject === null || reject === void 0 ? void 0 : reject(reason);
        },
        onExitState() {
            cleanup();
        },
    };
}
function ready(conn, options) {}
"""

_CJS_STUB = """\
const http2 = require("http2");
const node_error_js_1 = require("./node-error.js");
function connect(authority, http2SessionOptions) {
    const newConn = http2.connect(authority, http2SessionOptions);
    newConn.on("connect", onConnect);
    function onConnect() {}
    function onError(err) {
        reject((0, node_error_js_1.connectErrorFromNodeReason)(err));
    }
}
"""


def test_patches_esm_session_manager(tmp_path: Path):
    path = tmp_path / "http2-session-manager.js"
    path.write_text(_ESM_STUB)
    assert patch_session_manager(path) == "patched"
    text = path.read_text()
    assert http2_proxy_mark in text
    assert ORIGINAL_CONNECT not in text
    assert 'import * as http from "http";' in text
    assert "createConnection: () => tlsSock" in text
    assert "connectErrorFromNodeReason(err)" in text
    assert patch_session_manager(path) == "already"


def test_patches_cjs_session_manager(tmp_path: Path):
    path = tmp_path / "http2-session-manager.js"
    path.write_text(_CJS_STUB)
    assert patch_session_manager(path) == "patched"
    text = path.read_text()
    assert http2_proxy_mark in text
    assert ORIGINAL_CONNECT not in text
    assert 'const http = require("http");' in text
    assert "node_error_js_1.connectErrorFromNodeReason" in text


def test_rejects_unknown_session_manager(tmp_path: Path):
    path = tmp_path / "http2-session-manager.js"
    path.write_text("export class Http2SessionManager {}\n")
    with pytest.raises(CursorHttp2ProxyError, match="not the expected"):
        patch_session_manager(path)


def test_patch_installed_is_noop_without_cursor_sdk(monkeypatch):
    monkeypatch.setattr(
        "plaibook.cursor_http2_proxy._cursor_sdk_root",
        lambda _python: None,
    )
    assert patch_installed_cursor_sdk("/no/such/python") == []
