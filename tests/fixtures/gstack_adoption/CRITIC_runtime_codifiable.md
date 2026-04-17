# CRITIC__runtime.md — test fixture

## Verdict: PASS

All ACs verified.

## Evidence

AC-001: PASS — gate exits 2 on forbidden write

```
AC-001 evidence here
```

## Codifiable blocks

codifiable:
  - behavior: gate_exits_zero_on_allowed
    command: "echo hello"
    expected_exit: 0
    expected_stdout_contains: ["hello"]
    expected_stderr_contains: []

codifiable:
  - behavior: python_version_check
    command: "python3 --version"
    expected_exit: 0
    expected_stdout_contains: []
    expected_stderr_contains: []
