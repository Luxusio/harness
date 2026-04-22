# CRITIC__runtime.md — test fixture

## Verdict: PASS

All ACs verified.

## Evidence

AC-001: PASS — update_checks.py --help exits 0 with argparse usage output
AC-002: PASS — qa_codifier.py --help exits 0 with argparse usage output

## Codifiable blocks

codifiable:
  - behavior: update_checks_help_exits_zero
    ac_id: AC-001
    command: "python3 plugin/scripts/update_checks.py --help"
    expected_exit: 0
    expected_stdout_contains: ["usage"]
    expected_stderr_contains: []

codifiable:
  - behavior: qa_codifier_help_exits_zero
    ac_id: AC-002
    command: "python3 plugin/scripts/qa_codifier.py --help"
    expected_exit: 0
    expected_stdout_contains: ["usage"]
    expected_stderr_contains: []
