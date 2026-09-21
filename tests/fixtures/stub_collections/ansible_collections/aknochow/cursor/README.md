# Offline `aknochow.cursor.agent` stub

Playbook tests prepend this collection on `ANSIBLE_COLLECTIONS_PATH` so
`dispatch_cursor_lens_attempt.yml` can exercise rescue/bind/usage without
calling cursor-sdk. The module reads `CURSOR_AGENT_STUB_FILE` (JSON) and
`fail_json`/`exit_json`s that payload. It must not make a network call.
