# REQ process bash guard script execution

summary: mcp_bash_guard gates protected-artifact file mutation, not script execution
status: accepted
updated: 2026-08-26
freshness: current
confidence: high
kind: process
source: User-reported defect 2026-08-26 — ordinary commands such as `python3 scripts/gen.py` were denied as RECEIPTS.jsonl mutations (C-100). Scope settled the same day after a security review disproved the first fix's premise.

`plugin/scripts/mcp_bash_guard.py` guards the Bash surface for protected
artifacts. Its deny decisions must track real capability to write those
artifacts. Denying a command that cannot reach a protected artifact costs the
agent a work path and reports a false reason, which teaches contributors to
distrust or bypass the gate.

## Settled decision — script execution is not gated

The guard does **not** inspect or deny running a script. Deciding what a program
will do once it starts is left to agent discipline.

This is a deliberate narrowing, not an oversight. The guard previously read the
target script off disk and AST-scanned it for receipt writers. A security review
on 2026-08-26 demonstrated four independent bypasses of that inspection, each
proven by writing a forged PASS receipt, two of them through the live hook:

| Bypass | Why the inspection missed it |
|---|---|
| `python3 <<'EOF' … EOF` heredoc, `<<<` herestring, `<(…)` | `_tokenize` returns `<<` as a standalone token; it was mistaken for the script path, failed `lstat`, and fell through |
| `PYTHONPATH=<dir> python3 <missing file>` | `sitecustomize.py` executes at interpreter startup, before the missing script is opened |
| `printf … > /tmp/f.py && python3 /tmp/f.py` | Paths outside `repo_root` are ungated on the write side, so plant-then-run closed the loop |
| `python3 <script>.py -m json` | `-m` was detected anywhere in argv, not positionally, short-circuiting inspection |

A gate that is trivially bypassable while denying ordinary commands is worse
than no gate: it yields false assurance and trains agents to reach for
`HARNESS_SKIP_MCP_GUARD`. Receipt integrity cannot be enforced at the Bash
surface, so it is not claimed there.

**A previously documented premise is retracted.** The earlier fix asserted that
"an unresolvable path runs nothing; the interpreter fails on its own." That is
false — `PYTHONPATH` + `sitecustomize.py` and `.pth` files run code before the
script is opened. Any allow on a non-resolving path is also inherently
TOCTOU-provisional: the gate's decision describes the filesystem at check time,
and nothing re-checks it at exec time.

## Expected behavior

| Command shape | Expected | Why |
|---|---|---|
| `python3 <any path>`, resolvable or not, any size | allow | Execution is not gated |
| `python3 <path>` whose source imports a receipt writer | allow | Content of an executed file is not inspected |
| `python3 -` or piped/heredoc stdin | allow | Uninspectable, and denying it stopped nobody |
| Command text literally naming a lifecycle entrypoint or receipt symbol | deny | Cheap, reliable text match on the obvious case |
| `python -c` with command substitution or backticks (`$(…)`, `` `…` ``), quoted **or** unquoted | deny | The executed code is not the string the gate can parse, and the inline AST parse is what catches a one-line receipt write. The unquoted form leaves a bare `$` as the operand and needs its own check |
| `python -c` whose code contains a literal backtick | deny | Accepted over-block: quoting is not recoverable after tokenization, so a backtick cannot be told apart from a substitution |
| Obfuscated reference (`base64`, computed `__import__`) | allow | Not detectable without inspection this gate no longer performs |
| `tee`, `sed -i`, any redirect spelling, `cp`, `mv`, `install`, `rsync`, `truncate` **naming** a protected artifact, or copying into a directory that would produce one | deny | Direct file mutation — this is what the gate actually enforces |
| The same verbs reaching an artifact without naming it — `find … -exec`, `find … -delete`, `tar -C`/`unzip -d` unpacking over one, a bare filename after `cd` under an unrecognized executable | allow | The path is never a classifiable token. Recorded as a known gap, not a boundary |
| Hardlink or inode-alias route to **native Goal JSON** | deny | Identity evasion, inode-checked |
| Hardlink alias of a task-directory artifact (`RECEIPTS.jsonl`, `PLAN.md`, `TASK.json`) | allow once the alias exists | Only Goal JSON is inode-checked; task artifacts are matched by path and basename. Creating the alias in-band still denies |

## Requirements

- A deny reason must name the actual cause and must not claim a
  `RECEIPTS.jsonl` mutation for a command that performs none.
- The gate's scope is *file mutation of protected artifacts*. It must not be
  extended back into execution inspection without a design that survives the
  bypass table above.
- Receipt integrity is enforced by hook ownership of `RECEIPTS.jsonl` and by
  `task_verify` ordering, not by the Bash gate. Treat the Bash gate as a
  guardrail against accident, not a control against a determined caller. No
  document may describe it as the only control preventing a forged verdict.
- Known routes past it are documented rather than implied closed. The clearest
  is variable indirection (`F=<protected path>; echo x >> $F`), which allows
  because redirect targets are read from unexpanded tokens. See
  `doc/harness/patterns/mcp-bash-guard.md` § Known gaps.

## Known remaining friction (not fixed here)

- Non-python interpreters (`node`, `ruby`, …) were never inspected; that
  asymmetry is now moot since python is not inspected either.
- A read-only linter invoked with a gated path argument (`ruff check
  plugin/scripts/mcp_bash_guard.py`) is denied by `rule=workflow-control-surface`,
  and a task `MAINTENANCE` marker does not relax it. This also blocks
  `git checkout` of the guard itself, so reverting it requires the
  `HARNESS_SKIP_MCP_GUARD` escape or a non-Bash edit path. The relevant
  classifier is `_is_workflow_control_surface`, not `_embedded_path_candidates`.
- Inline `python -c` code is still AST-parsed, for filesystem writes and for
  shell-outs (`os.system`, `os.popen`, `subprocess.*`). Removing *script*
  inspection did not remove that. It denies the direct spellings — a one-line
  `open('…/RECEIPTS.jsonl','a').write(…)` and a shell-out carrying a protected
  path — but it is pattern-based, not a proof of impossibility: `python -c
  "$VAR"`, base64/`exec`, and computed names all pass. Command substitution
  around `-c` defeats the parse entirely, so it denies; that deny was dropped by
  accident when script inspection was removed and restored once review caught
  it.

## Verification

- `tests/test_mcp_bash_guard.py::TestMutationsAgainstProtectedArtifact::test_script_execution_is_not_inspected`
  and `::test_ordinary_script_execution_allows` cover the settled allow rows.
- `test_direct_lifecycle_hook_invocation_is_denied` remains the regression gate
  for literal-text denial.
- `test_existing_external_goal_hardlink_alias_denies` covers the inode-alias
  mutation route. It silently failed in setup until 2026-08-26 (it created
  `doc/harness/goals/` but not the `doc/harness/checkpoints/` directory it links
  into, so `os.link` raised `FileNotFoundError` before the guard ran). Note that
  hard links cannot cross filesystems, so the alias must sit on the repo's own
  device rather than in a tmpdir.
