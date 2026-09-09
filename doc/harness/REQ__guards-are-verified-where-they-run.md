---
tags: [harness, verification, guards, install, contracts, testing]
summary: 가드는 그것이 실행되는 환경에서 실행되어 검증되기 전까지 가드가 아니다. 그리고 커버리지 주장 자체도 기계로 확인된다 — 설치 트리 삭제 감지, 설치 후 런타임 스모크, 문서가 지목한 테스트 id 검증.
updated: 2026-09-09
freshness: current
invalidated_by_paths:
  - tests/conftest.py
  - tests/test_install_tree_removal_guard.py
  - plugin/scripts/install_smoke.py
  - tests/test_install_smoke.py
  - install.py
  - plugin/scripts/contract_lint.py
  - tests/test_contract_lint_real_tree.py
  - tests/test_contract_lint.py
  - plugin/skills/develop/verification-gate.md
---

# REQ — a guard is verified where it runs, and its coverage claim is checked

## Context

`TASK__session-rebinds-receipt-marker` (commit `6689dd7`) took eight review
rounds; seven returned FAIL and every finding was real. Read together, four of
them share one shape — **not a missing guard, but a guard aimed slightly off**,
whose failure mode is silence:

| # | The guard that existed | What it actually covered |
|---|---|---|
| 1 | the 2026-08-26 import-failure breadcrumb | walked up from the *script*, so it was inert in the installed tree — the only place hooks run |
| 2 | a fixture declared to pin a branch | never reached that branch; two guard branches survived whole-suite mutation |
| 3 | prose justifying the not-open branch | named a route (`park`) the code provably cannot produce |
| 4 | every round's invariant snapshot | covered `doc/harness/tasks/.active_sessions/`, never the install tree — which is what a mutation actually deleted |

Failure 1 cost a month, twice. Failure 4 destroyed the installed runtime
mid-task and was found by hand, not by review. The case study is
`REQ__receipt-subsystem-failures-are-observable.md`; this document states the
general requirement and what now enforces it.

## Expected behavior

### 1. A guard runs in the environment it protects

A check that has never executed where the failure occurs is not evidence. The
import breadcrumb resolved its repository from the script's ancestors, which
works in a checkout and finds nothing under
`~/.claude/harness-dev/plugin/scripts` — so the one environment that needed it
was the one environment where it could not fire. The same proposition applies
one level up: a test that does not actually reach the branch it names is not a
test of that branch.

### 2. A test run cannot silently remove a file from an installed runtime

`tests/conftest.py::install_trees_lose_no_files` inventories the three trees a
harness runtime executes from — `~/.claude/harness-dev`, `~/.codex/harness`, and
the versioned Codex plugin cache entry
`~/.codex/plugins/cache/harness/harness`, which is the tree Codex actually
loads and whose bytecode `install.py` prunes — at session start and again at
session end (path + size), and fails the run naming every path that
disappeared. Sibling marketplaces under `~/.codex/plugins/cache` belong to other
tools and are out of scope; watching them would cost more than it protects.
It is the detection layer behind two prevention layers —
`install._reject_real_install_root_under_test` and the `HARNESS_DEST` default
fixture — and must catch a removal even when both are bypassed, because a test
can reach those trees by any route, not only through the installer.

**Removals only, and `__pycache__` is out of scope.** Live session hooks write
bytecode into the install tree while the suite runs, and the installer prunes
those directories outright. A checksum or mtime comparison therefore reports a
change on every clean run — measured by hand during the prior task, where
`ls -la` reported CHANGED with nothing changed. A guard that cries wolf gets
switched off, and a switched-off guard is worse than none; the no-false-alarm
property is as load-bearing as the detection property, and
`test_a_run_that_only_adds_files_stays_green` pins it. Excluding `__pycache__`
costs no coverage of the runtime's own files: the `plugin/scripts` and
`plugin/mcp` sources that a `rmtree(cache.parent)` mutation actually deleted are
still inventoried. (Which *roots* are watched is settled above, not here.)
An absent tree (fresh machine, CI) inventories as empty and never fails.

The same check is standing review/QA procedure for work that runs outside
pytest — `plugin/skills/develop/verification-gate.md` Step 0.5. That snippet
must record paths only and exclude `__pycache__` for the same reasons, and it is
the one guard with no automated backstop, so
`test_the_documented_snapshot_matches_the_fixture` lifts the block out of the
doc, runs it, and fails if it disagrees with the fixture on either the roots or
the no-false-alarm property. Its first draft emitted `%p %s` including
`__pycache__`, which reported losses after every rewritten file and after the
`python3 install.py --force` repair the same paragraph prescribes.

### 3. The installed runtime is smoke-tested after every install

`plugin/scripts/install_smoke.py`, run by `install.py` after each runtime's
payload is synced, imports every registered hook module from the installed tree
and drives the installed `background_hook.py` against a throwaway repository
until a `started` receipt row appears. A failure fails the install with the
tree named.

It also runs on the `--if-stale` PAYLOAD_SYNCHRONIZED early return, which is the
path `install_verified.py` takes on every close and therefore the common one:
with the probe only on the post-sync branch, the only check that inspects what
actually runs would almost never run. Measured cost ~0.5s per runtime. On the
Codex skip path the subject is the cache entry Codex loads; when that entry is
absent there is nothing to probe, and the install step says so rather than
reporting a pass.

The suite tests the source tree; hooks execute from the installed tree. When
they diverge the suite is green and the runtime is dead — no receipt, no
`task_close`, and no signal of any kind. This is the only check that inspects
what actually runs.

Two properties of the probe are not incidental:

- It runs under the interpreter the runtime uses (`python3`), not
  `sys.executable`. Bytecode written by one CPython 3.12.13 build is rejected
  by another when `_lib` compares the imported code object against a fresh
  compile — measured 2026-09-09 between a venv interpreter and the system
  `python3`, in both directions. Probing with the wrong build reports failures
  the runtime never sees, and misses ones it does.
- It sets `PYTHONDONTWRITEBYTECODE=1`, so it never writes into the tree it
  inspects. Without that, the probe leaves cache entries that are themselves a
  cause of the outage it exists to detect.

Reproducing the outage requires a `.pyc` whose header mtime and size match the
source while the marshalled body differs. Arbitrary garbage bytecode is
rejected by the loader, which falls back to the source — a test built on it
passes against a dead runtime.

### 4. A durable doc cannot reference a test that does not exist

`contract_lint.check_doc_test_references` resolves every backticked `test_...`
identifier in a Git-tracked `doc/**/*.md` against the test functions and test
modules this repository defines, and reports the doc, the line, and the missing
id. R5 of the prior task found a mutation-table row naming a renamed test,
which made that row — the only coverage record for a guard branch —
unreproducible.

Scope is deliberately narrow, and each exclusion has a reason rather than an
allowlist:

- Identifiers that appear in Python sources without being test definitions are
  not coverage claims: `test_command` and `test_paths` are manifest keys
  documented under `doc/harness/patterns/`.
- File paths (`tests/test_stop_gate.py`) and anything suffixed are references
  to a file, not a claim about a function. The trailing lookaround must reject
  the *whole* token; a bare negative lookahead lets the regex backtrack and
  match `test_promote_learnings_current_ru` out of `..._run.py`.
- Dated notes under `doc/changes/` record the tree as it stood that day; a
  later rename does not falsify them.
- Gitignored trees under `doc/harness/` (tasks, retros, reviews) are working
  state, not durable knowledge.

Reported as a soft lint issue, in the same arrangement C-102 already uses for
the managed block and the weight budget: the build-failing enforcement is
`tests/test_contract_lint_real_tree.py`, which asserts the list is empty for
this repository and, by emptying the definition set, proves the scan reaches
real docs rather than nothing.

## Known ceiling (deliberate)

**Semantic falsehood in prose is not machine-checkable** (failure 3). AC-3
automates only the mechanical half: that a referenced test id exists. A
sentence that names a real test and describes it wrongly still passes.

**Mutation testing is not built here** (failure 2). It is the right tool for
"the test never reached the branch it names", and it is the only expensive
item — minutes rather than seconds — needing its own scoping against changed
files. Deferred so the cheap, high-yield checks land first. Until then, the
practice stands: prove a guard with a mutation, and record the mutation and the
test that goes red, as the case-study REQ does.

**The removal guard does not survive a hard kill.** Snapshot state lives in the
pytest session; a `SIGKILL`ed run leaves no comparison. The manual snapshot in
`verification-gate.md` covers the case that matters — deliberate mutation
experiments — because those are run knowingly.

## Rejected options

**Checksum or mtime comparison of the install trees.** Rejected: measured false
alarm on every run, from legitimate hook activity. Removals are the only
signal that is both meaningful and quiet.

**A `contract_lint` allowlist of non-test identifiers.** Rejected: an allowlist
rots, and each entry is a place where a genuine dangling reference can hide.
Resolving against the identifiers the sources actually use costs nothing and
answers the same question.

**Making the doc-reference check hard (non-zero exit).** Rejected for the
linter, which also runs in freshly set-up user projects where `doc/` may
reference tests that live elsewhere. The suite assertion gives this repository
the hard gate without exporting it as an install-time blocker.
