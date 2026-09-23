# -*- coding: utf-8 -*-
"""Patch cursor-sdk's vendored connect-node HTTP/2 client to use HTTPS_PROXY.

cursor-sdk local Send uses HTTP/2 to api2.cursor.sh. Node's http2.connect()
does a direct getaddrinfo and ignores HTTP(S)_PROXY. OpenShell-style
workers often have no working DNS and only allowlisted CONNECT through an
HTTP proxy. The unpatched client then idles after RUNNING (HTTP/1.1 through
the same proxy gets HTTP 464 from the HTTP/2-only agent endpoint).

This rewrites connect-node's Http2SessionManager connect() to open
CONNECT+TLS(ALPN h2) via the proxy, then http2.connect(createConnection).
Idempotent. No-op when the vendored file is missing.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

MARKER = "plaibookTlsViaHttpProxy"
ORIGINAL_CONNECT = "const newConn = http2.connect(authority, http2SessionOptions);"
SESSION_REL = (
    Path("_vendor")
    / "bridge"
    / "node_modules"
    / "@connectrpc"
    / "connect-node"
    / "dist"
)

_HELPERS = r"""
function plaibookProxyUrl() {
    return process.env.HTTPS_PROXY || process.env.https_proxy || process.env.HTTP_PROXY || process.env.http_proxy || "";
}
function plaibookBypassProxy(host) {
    const no = process.env.NO_PROXY || process.env.no_proxy || "";
    return no.split(/[\s,]+/).some((item) => item && (host === item || host.endsWith(`.${item}`)));
}
function plaibookTlsViaHttpProxy(authority) {
    const proxy = plaibookProxyUrl();
    let target;
    try {
        target = new URL(authority);
    }
    catch (_plaibookUrlErr) {
        return Promise.resolve(null);
    }
    if (!proxy || target.protocol !== "https:" || plaibookBypassProxy(target.hostname)) {
        return Promise.resolve(null);
    }
    const proxyUrl = new URL(proxy);
    const dest = `${target.hostname}:${target.port || "443"}`;
    return new Promise((resolve, reject) => {
        const req = http.request({
            host: proxyUrl.hostname,
            port: proxyUrl.port,
            method: "CONNECT",
            path: dest,
            headers: { Host: dest },
        });
        req.once("connect", (res, socket) => {
            if (res.statusCode !== 200) {
                socket.destroy();
                reject(new Error(`proxy CONNECT ${res.statusCode} for ${dest}`));
                return;
            }
            const tlsSock = tls.connect({
                socket,
                servername: target.hostname,
                ALPNProtocols: ["h2"],
            }, () => resolve(tlsSock));
            tlsSock.once("error", reject);
        });
        req.once("error", reject);
        req.end();
    });
}
"""


class CursorHttp2ProxyError(RuntimeError):
    """Vendored connect-node HTTP/2 proxy patch could not be applied."""


def patch_installed_cursor_sdk(python: str | None = None) -> list[str]:
    """Patch esm+cjs session managers in ``python``'s cursor-sdk. Returns statuses."""
    exe = python or sys.executable
    results: list[str] = []
    for path in _session_manager_paths(exe):
        results.append(f"{path.name}:{patch_session_manager(path)}")
    return results


def patch_session_manager(path: Path) -> str:
    """Patch one http2-session-manager.js. Returns patched, already, or skipped."""
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return "already"
    if ORIGINAL_CONNECT not in text:
        raise CursorHttp2ProxyError(
            f"{path} is not the expected connect-node Http2SessionManager "
            "(missing http2.connect(authority, http2SessionOptions)). "
            "cursor-sdk local Send may hang behind an HTTP proxy with no DNS."
        )
    updated = _ensure_http_tls_imports(text)
    updated = _replace_connect_function(updated)
    if MARKER not in updated:
        raise CursorHttp2ProxyError(f"{path} patch produced no marker")
    path.write_text(updated, encoding="utf-8")
    return "patched"


def _session_manager_paths(python: str) -> list[Path]:
    root = _cursor_sdk_root(python)
    if root is None:
        return []
    found: list[Path] = []
    for variant in ("esm/http2-session-manager.js", "cjs/http2-session-manager.js"):
        path = root / SESSION_REL / variant
        if path.is_file():
            found.append(path)
    return found


def _cursor_sdk_root(python: str) -> Path | None:
    if _same_executable(python):
        try:
            import cursor_sdk
        except ImportError:
            return None
        return Path(cursor_sdk.__file__).resolve().parent
    probe = (
        "import cursor_sdk, pathlib, sys\n"
        "print(pathlib.Path(cursor_sdk.__file__).resolve().parent)\n"
    )
    try:
        completed = subprocess.run(
            [python, "-c", probe],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    line = completed.stdout.strip().splitlines()
    return Path(line[-1]) if line else None


def _same_executable(python: str) -> bool:
    try:
        return Path(python).resolve() == Path(sys.executable).resolve()
    except OSError:
        return python == sys.executable


def _ensure_http_tls_imports(text: str) -> str:
    if re.search(r'import \* as http from "http"', text) or re.search(
        r'const http = require\("http"\)', text
    ):
        return text
    esm = re.search(r'^import \* as http2 from "http2";\n', text, re.M)
    if esm:
        return text.replace(
            esm.group(0),
            esm.group(0) + 'import * as http from "http";\nimport * as tls from "tls";\n',
            1,
        )
    cjs = re.search(r'^const http2 = require\("http2"\);\n', text, re.M)
    if cjs:
        return text.replace(
            cjs.group(0),
            cjs.group(0) + 'const http = require("http");\nconst tls = require("tls");\n',
            1,
        )
    raise CursorHttp2ProxyError("cannot insert http/tls imports into http2-session-manager.js")


def _replace_connect_function(text: str) -> str:
    start = text.find("function connect(authority, http2SessionOptions) {")
    if start < 0:
        raise CursorHttp2ProxyError("connect(authority, http2SessionOptions) not found")
    end = _matching_brace_end(text, text.find("{", start))
    original = text[start:end]
    if "node_error_js_1" in original:
        on_error = (
            "reject === null || reject === void 0 ? void 0 : "
            "reject((0, node_error_js_1.connectErrorFromNodeReason)(err));"
        )
    else:
        on_error = (
            "reject === null || reject === void 0 ? void 0 : reject(connectErrorFromNodeReason(err));"
        )
    replacement = _CONNECT_FN.replace("/*ON_ERROR*/", on_error)
    helpers = _HELPERS.strip() + "\n"
    return text[:start] + helpers + replacement + text[end:]


def _matching_brace_end(text: str, open_idx: int) -> int:
    depth = 0
    i = open_idx
    in_str = None
    escape = False
    while i < len(text):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == in_str:
                in_str = None
        elif ch in ('"', "'", "`"):
            in_str = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise CursorHttp2ProxyError("unbalanced braces in connect()")


_CONNECT_FN = """function connect(authority, http2SessionOptions) {
    let resolve;
    let reject;
    const conn = new Promise((res, rej) => {
        resolve = res;
        reject = rej;
    });
    let newConn;
    let aborted = false;
    function onConnect() {
        resolve === null || resolve === void 0 ? void 0 : resolve(newConn);
        cleanup();
    }
    function onError(err) {
        /*ON_ERROR*/
        cleanup();
    }
    function cleanup() {
        if (newConn) {
            newConn.off("connect", onConnect);
            newConn.off("error", onError);
        }
    }
    plaibookTlsViaHttpProxy(authority).then((tlsSock) => {
        if (aborted) {
            tlsSock === null || tlsSock === void 0 ? void 0 : tlsSock.destroy();
            return;
        }
        const opts = tlsSock
            ? Object.assign({}, http2SessionOptions, { createConnection: () => tlsSock })
            : http2SessionOptions;
        newConn = http2.connect(authority, opts);
        newConn.on("connect", onConnect);
        newConn.on("error", onError);
    }, onError);
    return {
        t: "connecting",
        conn,
        abort(reason) {
            aborted = true;
            if (newConn && !newConn.destroyed) {
                newConn.destroy(undefined, http2.constants.NGHTTP2_CANCEL);
            }
            reject === null || reject === void 0 ? void 0 : reject(reason);
        },
        onExitState() {
            cleanup();
        },
    };
}"""
