# REQ process bash guard script execution

summary: mcp_bash_guard gates protected-artifact file mutation only; execution is never gated
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

## Settled decision — code execution is not gated, at all

The guard does **not** inspect or deny running a script, and does **not** inspect
inline `python -c` code. Deciding what a program will do once it starts is left
to agent discipline.

The inline-code half was removed on 2026-08-27 after six review rounds. Each
round found another spelling that reached the same write — command substitution
quoted then unquoted, `$VAR` indirection, base64/`exec`, computed imports,
`os.system`, argv-list `subprocess` — and two of the attempted fixes were worse
than the gap they closed: one denied ordinary
`subprocess.run(['pytest', <path>])`, and one recursed on a self-matching glob
name until the entire guard failed open. Chasing program semantics off a command
line is not winnable at this layer, and each attempt added surface that had to
be reviewed again.

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
| Command text literally naming a lifecycle entrypoint or receipt symbol | allow | Execution is not gated. `python3 plugin/scripts/background_hook.py` runs |
| `python -c <any code>`, `node -e`, `perl -e`, `ruby -e` | allow | Inline code is not inspected |
| An unrecognized executable carrying a gated path as an argument (`ruff check <path>`, `ed <path>`) | allow | The branch that denied these could not tell a reader from a writer, and blocked repairing the guard itself |
| Obfuscated reference (`base64`, computed `__import__`) | allow | Not detectable without inspection this gate no longer performs |
| `tee`, `sed -i`, any redirect spelling, `cp`, `mv`, `install`, `rsync`, `truncate`, `touch` **naming** a protected artifact, or copying into a directory that would produce one | deny | Direct file mutation — this is what the gate actually enforces, subject to the per-verb option model being complete for the spelling used |
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
- Known routes past it are documented rather than implied closed. Two classes
  carry the weight. First, the guard re-implements a partial getopt per verb, so
  an unmodelled option spelling can hide the destination. Second, it reads token
  *text* as shell syntax, so it must know how each token was quoted; it
  establishes that by lexing twice and requiring the two lexes to agree. When
  they cannot be reconciled it does **not** pick a reading — picking either one
  is a laundering direction — it classifies under both and denies if either
  would. See `doc/harness/patterns/mcp-bash-guard.md` § Known gaps.

## Known remaining friction (not fixed here)

- Non-python interpreters (`node`, `ruby`, …) were never inspected; that
  asymmetry is now moot since python is not inspected either.
- A read-only linter with a gated path argument (`ruff check
  plugin/scripts/mcp_bash_guard.py`) now **allows**: the branch that denied it
  could not tell a reader from a writer.
- `git checkout HEAD -- <gated path>` still denies, including for the guard
  itself, so reverting this file over Bash needs the `HARNESS_SKIP_MCP_GUARD`
  escape or a non-Bash edit path. That deny is correct — `git checkout` really
  does rewrite the working tree — but be aware of it before relying on Bash to
  repair the gate.
- Inline `python -c` code is **not** parsed. A one-line
  `open('…/RECEIPTS.jsonl','a').write(…)` allows. This is the settled decision
  above, not an oversight: the AST parse that used to catch it was removed after
  six review rounds showed the deny could not be made to hold without adding
  more surface than it protected.
- Do not reintroduce inline-code inspection to close a newly-found spelling.
  The next spelling always exists. If receipt integrity needs strengthening,
  strengthen hook ownership or `task_verify`, which are the actual controls.

## Verification

- `tests/test_mcp_bash_guard.py::TestMutationsAgainstProtectedArtifact::test_ordinary_script_execution_allows`
  and `tests/test_mcp_bash_guard_readonly_inspection.py::test_python_inline_receipt_write_allowed`
  cover the settled allow rows.
- The deny rows are covered by `test_each_verb_denies_source`,
  `test_alternate_redirect_operators_deny`, `test_copy_into_task_directory_denies`,
  `test_target_directory_option_denies`, `test_newline_does_not_launder_a_mutator`,
  `test_leading_shell_control_word_does_not_launder`, and
  `test_working_tree_rewriting_git_subcommands_still_denied`.
- `test_existing_external_goal_hardlink_alias_denies` covers the inode-alias
  mutation route. It silently failed in setup until 2026-08-26 (it created
  `doc/harness/goals/` but not the `doc/harness/checkpoints/` directory it links
  into, so `os.link` raised `FileNotFoundError` before the guard ran). Note that
  hard links cannot cross filesystems, so the alias must sit on the repo's own
  device rather than in a tmpdir.
