#!/usr/bin/python
# Copyright: (c) 2026, Adam Knochowski (@aknochow)
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline stub of aknochow.cursor.agent for plaibook playbook tests.

Reads CURSOR_AGENT_STUB_FILE (JSON object). If the object has failed=true,
fail_json with the remaining keys (the WaitLiveRun-after-drain shape).
Otherwise exit_json. Never calls cursor-sdk or the network.
"""

from __future__ import annotations

import json
import os

from ansible.module_utils.basic import AnsibleModule

DOCUMENTATION = r"""
---
module: agent
short_description: Offline stub of aknochow.cursor.agent
description:
  - Test double. Returns the JSON object in E(CURSOR_AGENT_STUB_FILE).
  - Does not call cursor-sdk.
author:
  - Adam Knochowski (@aknochow)
options:
  prompt:
    type: str
    required: true
  model:
    type: str
    required: true
  effort:
    type: str
  tools:
    type: list
    elements: str
  structured_tool:
    type: dict
  agents:
    type: dict
  setting_sources:
    type: list
    elements: str
  api_key:
    type: str
  cwd:
    type: path
    required: true
"""


def main():
    module = AnsibleModule(
        argument_spec=dict(
            prompt=dict(type="str", required=True),
            model=dict(type="str", required=True),
            effort=dict(type="str"),
            tools=dict(type="list", elements="str"),
            disallowed_tools=dict(type="list", elements="str"),
            structured_tool=dict(type="dict"),
            agents=dict(type="dict"),
            setting_sources=dict(type="list", elements="str", default=[]),
            mode=dict(type="str"),
            bridge_timeout=dict(type="float"),
            api_key=dict(type="str", no_log=True),
            cwd=dict(type="path", required=True),
        ),
        supports_check_mode=False,
    )

    path = os.environ.get("CURSOR_AGENT_STUB_FILE", "")
    if not path:
        module.fail_json(msg="CURSOR_AGENT_STUB_FILE is unset; refusing a live Cursor call")
        return
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        module.fail_json(msg=f"cursor agent stub failed to read {path}: {exc}")
        return
    if not isinstance(payload, dict):
        module.fail_json(msg="cursor agent stub payload must be a JSON object")
        return

    failed = bool(payload.pop("failed", False))
    payload["stub"] = True
    if failed:
        msg = payload.pop("msg", "cursor agent stub failure")
        module.fail_json(msg=msg, **payload)
        return
    module.exit_json(changed=False, **payload)


if __name__ == "__main__":
    main()
