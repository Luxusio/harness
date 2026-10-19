# Installer feedback

## Intent and provenance

Accepted user requirement: installation should be simpler to understand.
Source: conversation summary preserved in
`doc/harness/tasks/TASK__install-and-device-lab/PLAN.md`, exact user quote:
“install이거 너무 복잡한데 좀 간단하게 만들어줘”.

The behavior below is derived design supporting that request, not additional
user requirements. Its basis is the existing unified installer's structured
`InstallResult` and the standing constraint to preserve installation checks,
runtime selection, destinations, exit codes and safe recovery.

## Derived observable behavior

- `python3 install.py` reports a concise result for each detected runtime,
  preserving `STATUS: APPLIED`, `SKIPPED`, `DRY_RUN` or `ERROR`. A synchronized
  payload is described as a payload result, without claiming configuration
  health. A refreshed Claude cache remains an applied result.
- Normal output omits routine steps, backups, repository headers and repeated
  payload/configuration drift explanations. `--verbose` exposes these details
  and full dry-run plans through the same installer execution.
- Failures retain their cause and actionable recovery in either mode, including
  failed runtime-smoke diagnostics. Detailed errors are not truncated to meet
  an arbitrary output limit. Any failed runtime still produces exit 1.
- Recovery preserves prerequisite order: repair the reported cause, perform
  required verification, then retry the affected runtime. Unsafe-ancestor
  recovery prints its existing remedy before retrying without `--force`;
  daemon recovery retains its exact configuration and verification command.
  Printed commands are instructions, never automatically executed.
- Dry-run visibly identifies planned work and explains that applying requires
  removing `--dry-run`; hidden details do not leave a reference to “steps above”.
- Automatic runtime detection, explicit runtime flags, missing-runtime notices,
  conflicting flags, comparison/smoke/ownership guards and exit codes remain
  unchanged. This feedback change performs no automatic permission repair,
  destination switch or runtime omission to obtain success.

## Examples and verification

A successful ordinary run produces one result line per runtime, such as
`✓ [claude] STATUS: APPLIED — Claude install complete`. An unsafe-path error
reports its cause and the existing `repair: …, then re-run: …` instruction.
Use `python3 install.py --verbose` for the full trace and `--dry-run --verbose`
to inspect a full plan without applying it.

Behavioral evidence: `tests/test_installer_output.py`,
`tests/test_install_unsafe_ancestor_remedy.py`,
`tests/regression/task__unified_install/test_install_py.py`, and the ordered
daemon recovery test in `tests/test_codex_runtime_reload.py`.
