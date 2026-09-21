#!/usr/bin/env bash
set -euo pipefail

# Runs all deterministic, offline Ansible playbook tests that require no live models,
# external API keys, or sandbox clusters.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"
export ANSIBLE_CONFIG="${REPO_ROOT}/ansible.cfg"

PLAYBOOKS=(
  "tests/test_checklist_execution.yml"
  "tests/test_cursor_named_lens_retry.yml"
  "tests/test_cursor_lens_attempt_usage.yml"
  "tests/test_cursor_prompt_nonce.yml"
  "tests/test_guardian_not_installed.yml"
  "tests/test_guardian_scan.yml"
  "tests/test_merge_dedup.yml"
  "tests/test_merge_findings_string_encoding.yml"
  "tests/test_merge_self_refuted_filter.yml"
  "tests/test_neutralization_check.yml"
  "tests/test_persisted_path_collision.yml"
  "tests/test_pipeline_stats.yml"
  "tests/test_pr_ci_preflight.yml"
  "tests/test_pr_merge_base_diff.yml"
  "tests/test_resolve_target_pr_parsing.yml"
  "tests/test_sandbox_unreachable_teardown.yml"
  "tests/test_verify_score_recompute.yml"
)

echo "Running ${#PLAYBOOKS[@]} offline Ansible playbook test(s)..."

# dispatch_cursor_lens_attempt.yml calls aknochow.cursor.agent. The
# named-lens retry playbook stubs that module (no live Cursor) by
# putting tests/fixtures/stub_collections first on the collections path.
STUB_COLLECTIONS="${REPO_ROOT}/tests/fixtures/stub_collections"

for pb in "${PLAYBOOKS[@]}"; do
  echo "--- Running ${pb} ---"
  if [[ "${pb}" == "tests/test_cursor_named_lens_retry.yml" ]]; then
    ANSIBLE_COLLECTIONS_PATH="${STUB_COLLECTIONS}${ANSIBLE_COLLECTIONS_PATH:+:${ANSIBLE_COLLECTIONS_PATH}}" \
      ansible-playbook "${pb}"
  else
    ansible-playbook "${pb}"
  fi
done

echo "--- Running tests/run_cursor_sidecar_skip.sh ---"
bash "${REPO_ROOT}/tests/run_cursor_sidecar_skip.sh"

echo "All ${#PLAYBOOKS[@]} playbook tests plus sidecar skip scenarios passed successfully."
