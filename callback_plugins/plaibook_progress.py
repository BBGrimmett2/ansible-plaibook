# -*- coding: utf-8 -*-
"""Write the current review stage for the plaibook CLI spinner.

Aggregate callback: does not replace the default stdout callback. No-ops
unless PLAIBOOK_PROGRESS_FILE names a CLI-owned regular file under a
private temp directory. Mapping lives in plaibook.progress so tests and
this plugin stay in sync; ansible-playbook without the plaibook package
installed still applies the same path checks before any write.
"""

from __future__ import annotations

import os
import stat
import tempfile

from ansible.plugins.callback import CallbackBase

try:
    from plaibook.progress import (
        allowed_progress_path,
        clone_url_from_facts,
        clone_url_from_task_args,
        format_stage_line,
        stage_for_task,
        write_progress_line,
    )
except ImportError:  # pragma: no cover - AAP/EE path without the CLI package
    # Keep these checks identical to plaibook.progress: this is the
    # privileged write path when the CLI package is not installed.

    def stage_for_task(name: str) -> str | None:
        return None

    def format_stage_line(stage: str, *, clone_url: str | None = None) -> str:
        return stage

    def clone_url_from_facts(facts: object) -> str | None:
        return None

    def clone_url_from_task_args(args: object) -> str | None:
        return None

    _PROGRESS_PREFIX = "plaibook-progress-"
    _PROGRESS_SUFFIX = ".txt"
    _PROGRESS_MAX_BYTES = 512

    def _progress_allowed_roots() -> list[str]:
        roots: list[str] = []
        cache_tmp = os.path.join(
            os.path.expanduser("~"), ".cache", "ansible-plaibook", "tmp"
        )
        for raw in (tempfile.gettempdir(), cache_tmp):
            try:
                real = os.path.realpath(raw)
            except OSError:
                continue
            if real not in roots:
                roots.append(real)
        return roots

    def allowed_progress_path(path: str) -> str | None:
        raw = (path or "").strip()
        if not raw or "\x00" in raw or not os.path.isabs(raw):
            return None
        name = os.path.basename(raw)
        if not name.startswith(_PROGRESS_PREFIX) or not name.endswith(_PROGRESS_SUFFIX):
            return None
        if os.sep in name or (os.altsep and os.altsep in name):
            return None
        try:
            real_parent = os.path.realpath(os.path.dirname(raw))
            under_root = False
            for root in _progress_allowed_roots():
                try:
                    if os.path.commonpath([root, real_parent]) == root:
                        under_root = True
                        break
                except ValueError:
                    continue
            if not under_root:
                return None
            parent_stat = os.stat(real_parent)
        except (OSError, ValueError):
            return None
        if not stat.S_ISDIR(parent_stat.st_mode):
            return None
        if parent_stat.st_uid != os.geteuid():
            return None
        if stat.S_IMODE(parent_stat.st_mode) & 0o077:
            return None
        if not os.path.basename(real_parent).startswith(_PROGRESS_PREFIX):
            return None
        return os.path.join(real_parent, name)

    def write_progress_line(path: str, line: str) -> bool:
        if not hasattr(os, "O_NOFOLLOW"):
            return False
        resolved = allowed_progress_path(path)
        if resolved is None:
            return False
        text = (line or "").splitlines()[0][:_PROGRESS_MAX_BYTES]
        flags = os.O_WRONLY | os.O_NOFOLLOW | os.O_TRUNC | getattr(os, "O_CLOEXEC", 0)
        fd = -1
        try:
            fd = os.open(resolved, flags)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                return False
            if info.st_uid != os.geteuid():
                return False
            if stat.S_IMODE(info.st_mode) & 0o077:
                return False
            os.write(fd, (text + "\n").encode("utf-8"))
            return True
        except OSError:
            return False
        finally:
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass

DOCUMENTATION = """
    name: plaibook_progress
    type: aggregate
    short_description: Write coarse review stages for the plaibook spinner.
    description:
      - When PLAIBOOK_PROGRESS_FILE names a CLI-owned regular file under
        the process temp directory or ~/.cache/ansible-plaibook/tmp
        (prefix plaibook-progress-), writes a one-line stage name each
        time the review moves to a new main stage. Other values are
        ignored. Checkout includes review_clone_url / the git module
        repo when that fact is set.
    requirements: []
"""


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "aggregate"
    CALLBACK_NAME = "plaibook_progress"
    CALLBACK_NEEDS_ENABLED = False

    def __init__(self):
        super().__init__()
        raw = (os.environ.get("PLAIBOOK_PROGRESS_FILE") or "").strip()
        self._path = raw if allowed_progress_path(raw) else ""
        self._stage = ""
        self._clone_url = ""
        self._line = ""

    def v2_playbook_on_task_start(self, task, is_conditional):
        if not self._path:
            return
        name = ""
        args = {}
        if task is not None:
            name = getattr(task, "get_name", lambda: "")() or str(getattr(task, "name", "") or "")
            raw_args = getattr(task, "args", None)
            if isinstance(raw_args, dict):
                args = raw_args
        url = clone_url_from_task_args(args)
        if url:
            self._clone_url = url
        stage = stage_for_task(name)
        if stage:
            self._stage = stage
        if not self._stage:
            return
        self._write_line()

    def v2_runner_on_ok(self, result):
        if not self._path:
            return
        payload = getattr(result, "_result", None) or {}
        url = clone_url_from_facts(payload.get("ansible_facts"))
        if not url:
            invocation = payload.get("invocation") or {}
            module_args = invocation.get("module_args") or {}
            url = clone_url_from_task_args(module_args)
        if not url:
            return
        self._clone_url = url
        if self._stage == "checkout" or not self._stage:
            if not self._stage:
                self._stage = "checkout"
            self._write_line()

    def _write_line(self) -> None:
        line = format_stage_line(self._stage, clone_url=self._clone_url)
        if not line or line == self._line:
            return
        if not write_progress_line(self._path, line):
            if allowed_progress_path(self._path) is None:
                self._path = ""
            return
        self._line = line
