# Installer feedback

## User requirements and provenance

The user explicitly requested simpler output (“install이거 너무 복잡한데 좀 간단하게 만들어줘”), one success/failure outcome per runtime and an error message on failure (“그냥 클러드 성공 실패 코덱스 성공 실패 이렇게 주고 에러메세지 있으면 띄우고 그렇게 하면 안되나?”), then English (“영어로 해라”).

## Derived observable behavior

The exact labels and verbosity mechanics below implement those requests; they are implementation decisions rather than additional user requirements. They supersede the earlier derived STATUS-token preservation.

- Default output is one English result line per selected runtime: `Claude: SUCCESS` and `Codex: SUCCESS`. Failure uses `FAILED`, followed by `Error: <cause>`.
- A successful preview uses `DRY RUN`; it never claims installation succeeded. Runtime failures remain FAILED even in dry-run and return exit 1.
- Routine headers, steps, backups, missing-runtime notices and footers appear only with `--verbose`, where applicable. Full recovery instructions and smoke diagnostics remain available in verbose output. Error causes remain visible without verbose.
- Automatic runtime selection, explicit flags, conflicting-flag handling, destination and trust/permission checks, recovery prerequisite order and install execution remain unchanged. No automatic permission repair or runtime omission obtains a success.

## Verification

`tests/test_installer_output.py` verifies both verbosity modes, preview, mixed failure, errors, runtime selection and explicit recovery. `tests/test_install_unsafe_ancestor_remedy.py` and `tests/regression/task__unified_install` preserve safety and command behavior.
