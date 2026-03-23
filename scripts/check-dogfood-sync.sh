#!/usr/bin/env bash
# check-dogfood-sync.sh
# Detects drift between dogfood fixtures (harness/) and setup templates
# (plugin/skills/setup/templates/harness/).
#
# Only static mirror files are compared. Dynamic files (manifest.yaml,
# approvals.yaml, current-task.yaml, session/state files) are excluded.
#
# Exit codes:
#   0 — no drift detected
#   1 — drift or missing files detected

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

TEMPLATE_BASE="${REPO_ROOT}/plugin/skills/setup/templates"
DOGFOOD_BASE="${REPO_ROOT}/harness"

declare -a PAIRS=(
  "harness/router.yaml"
  "harness/scripts/validate.sh"
  "harness/scripts/smoke.sh"
  "harness/scripts/arch-check.sh"
  "harness/scripts/check-approvals.sh"
  "harness/scripts/build-memory-index.sh"
  "harness/scripts/build-memory-index.py"
  "harness/scripts/check-memory-index.sh"
  "harness/scripts/query-memory.sh"
  "harness/scripts/query-memory.py"
  "harness/docs/requirements/README.md"
)

drift=0

for rel in "${PAIRS[@]}"; do
  # Build paths relative to repo root
  template_rel="plugin/skills/setup/templates/${rel}"
  dogfood_rel="${rel}"

  template="${REPO_ROOT}/${template_rel}"
  dogfood="${REPO_ROOT}/${dogfood_rel}"

  template_exists=false
  dogfood_exists=false

  [[ -f "${template}" ]] && template_exists=true
  [[ -f "${dogfood}" ]] && dogfood_exists=true

  if ! $template_exists && ! $dogfood_exists; then
    echo "MISSING: both ${template_rel} and ${dogfood_rel} do not exist"
    drift=1
  elif ! $template_exists; then
    echo "MISSING: ${template_rel}"
    drift=1
  elif ! $dogfood_exists; then
    echo "MISSING: ${dogfood_rel}"
    drift=1
  else
    if ! diff -q "${template}" "${dogfood}" > /dev/null 2>&1; then
      echo "DRIFT: ${dogfood_rel} differs from ${template_rel}"
      drift=1
    fi
  fi
done

if [[ "${drift}" -eq 0 ]]; then
  echo "No drift detected."
  exit 0
else
  exit 1
fi
