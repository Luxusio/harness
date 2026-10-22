# Shared verification execution

## Request provenance

The coordinator relayed these exact user excerpts from the current conversation;
no message identifier was supplied. Only the intent rows are user requirements.
The CLI, schema and lease design below are derived implementation mechanics.

| Intent | Provenance and source locator | Exact excerpt |
|---|---|---|
| Share preparation and parallelize readers without a general scheduler | user_explicit; message opening “CCC 프로젝트에서 Harness를 장기간 사용하며 드러난 개발·병렬 검증·재개 과정의 비효율을 개선해 줘.”, section 2 | “동일 소스를 대상으로 읽기 전용 리뷰·검증은 병렬 실행하고, 공통 산출물 생성은 한 번만 수행하는 흐름이 필요하다. 이를 위해 범용 DAG 엔진이나 새로운 스케줄러를 추가하지 마라.” |
| Bind reuse to relevant inputs | user_explicit; same message, section 4 bullet | “기존 결과를 코드·빌드 입력·플랫폼·명령 범위에 맞게 재사용.” |
| Recheck changed and affected scopes | user_explicit; later message opening ““병렬로 해라”를 권장하는 대신, 작업 배정 단계에서 강제해야 해.”, item 4 | “변경되지 않은 코드의 검증은 재사용하고, 수정된 범위와 영향받는 부분만 다시 검증.” |
| Lock conflicting resources while independent work proceeds | user_explicit; same later message, item 5 | “같은 VM·디스플레이·빌드 출력에는 자원 잠금을 적용. 독립 작업까지 함께 멈추지 않도록 함.” |

## Derived execution contract

Derived from those intents and CONTRACTS C-04/C-14: `verify_runner.py --jobs`
executes a bounded JSON array of argv jobs. Each invocation represents **at most
one producer and its read-only consumers**. The producer completes once before
its consumers enter the existing ThreadPool. Independent producer groups use
separate invocations and separate result files; no group waits for unrelated
producers. Legacy manifest/string commands keep their existing behavior and do
not gain reusable job proofs.

Each job declares `job_id`, `outcome_id`, `kind` (`producer` or `consumer`),
`argv`, and `inputs` with explicit `source` roots and optional `artifacts`,
`exclude`, `deny` lists, using the existing verification-target input policy.
Optional lists are `outputs`, `resources`, `requires`, `environment`, and
`toolchains`. Consumers have no outputs; producer outputs name regular files,
not directories. Unknown fields, duplicate IDs, multiple producers, symlinks,
traversal, output/source overlap and results/input/output overlap are rejected
before launch. Producers cannot consume their own outputs. Required common,
build and lockfile inputs belong in the declared scopes; review owns adequacy.

Commands execute from the exact `--root`, including a frozen view without
`.git`. They receive only declared environment names plus `PATH`; declare
`HOME`, locale, display, proxy or other necessary values explicitly. Environment
values are hashed in context rather than stored. Command output and argv are
diagnostic data and may themselves contain sensitive text. The resolved command
executable and declared toolchains bind binary bytes and successful `--version`
output using the existing verification-target API. A tool without a usable
version identity is unavailable for reusable execution. Arbitrary shell-command
reuse is unsupported.

`requires` lists host-provided agent capability names (supplied through repeated
`--capability`) and/or automatically detected `platform:<sys.platform>` and
`executable:<name>` requirements. The host must supply actual available agent
capabilities; the runner does not infer remote desktop, VM, GPU or browser access
from an executable or exit code. Missing capability prevents that job from
starting. Failed preparation blocks its consumers.

## Derived result and reuse contract

`--results` names one **per-group** durable JSON file outside selected inputs and
outputs, independently of source checkout. Protected lifecycle artifacts cannot
be used as result stores. The schema is `harness.verification-execution`,
`version: 1`, with `task_id`, `run_id`, `execution_generation`, aggregate
`status`/`returncode` and declaration-ordered `commands`. Atomic intermediate
publication has aggregate FAIL until all jobs successfully finish; readers need
no lock. Corrupt, missing, foreign-task/run or unknown-version data is unavailable
for reuse and is regenerated. A per-group store lease prevents concurrent
duplicate preparation; unrelated groups must use different result paths.

Completed records contain `job_id`, `outcome_id`, `kind`, `argv`, `scope`,
`outputs`, `input_fingerprint`, `context_fingerprint`, `artifact_fingerprint`, `artifact_manifest`,
`state`, `status`, `returncode`, `reused`, `origin_generation`, and
`execution_generation`. Input/context fields may be absent when preparation or
measurement never completed. `state` distinguishes `completed`,
`command_failure`, `start_failed`, `timeout`, `executor_error`,
`capability_unavailable`, and `blocked_dependency`. Nonzero exit is only a
command failure; it does not establish product versus fixture ownership.

Reuse requires a completed successful record from the same task/run/job/outcome,
matching scoped input bytes, declaration, argv, environment, platform, toolchain
and current output bytes. A generation labels an invocation; advancing it does
not invalidate identical inputs. Inputs/context are checked before and after
execution. A change produces `executor_error`, never reusable PASS. Missing
producer output triggers regeneration; consumers compare the regenerated bytes,
so identical artifact content preserves their proof. Relevant consumer source
changes invalidate that consumer only. Failed or partial producer output cannot
authorize its consumers. Each consumer also compares any selected prepared
artifact against its producer's recorded manifest after acquiring read leases;
replacement by another group fails applicability rather than silently accepting
another preparation.

Relative input/output names and relative command arguments permit reuse across
byte-identical restored/frozen views with the same external result file. Absolute
argv values are part of identity. Commands must declare all relevant inputs and
must not depend on undeclared checkout-location state; such a command is outside
the reusable contract. These are cooperative trusted commands, not a sandbox.

```sh
python3 plugin/scripts/verify_runner.py --root /exact/source-view \
  --jobs /task/work/group.json --results /task/work/group-results.json \
  --task-id TASK__example --run-id current-run --generation generation-2 \
  --max-workers 4 --timeout 60
```

The same invocation against a restored source root, using the same declared
relative inputs and result file, validates/reuses that group's existing results.
This allows substantive QA and a later outcome assertion to consult the same
execution rather than rerun unchanged product commands for bookkeeping.
`--reuse-only` validates the same scoped successful job records but never runs
their argv on a miss; the job returns `executor_error` with no exit code and an
explicit reuse-only diagnostic. It still measures inputs and probes toolchain
identity. Use this mode for later bookkeeping assertions that must not repeat
product tests. A missing or stale proof does not pass.
Execution JSON is advisory data: it neither creates native QA receipts nor grants
WBS credit, runtime PASS or close authority. Independent roles still own review
and QA and the existing WBS gates still decide admission.

## Derived cooperative ownership contract

Stable flock files under `~/.cache/harness-verification-leases` coordinate local
Harness participants across tasks, runs, worktrees and processes. Resource keys
are exact host-wide strings, for example `display::99`, `vm:windows-qa`,
`gpu:0`, `audio:default`, `port:8123`, or `build:shared-cache`. Callers declare
the actual shared identity; the runner does not reserve OS ports, create VMs or
control nonparticipating processes.

Artifact/output keys use canonical absolute file paths. Ancestor shared locks
and exclusive output-leaf locks serialize overlapping writers while sibling
outputs and concurrent readers remain independent. Consumers take shared artifact
locks. All keys are acquired in sorted order; a failed partial acquisition
releases everything and leaves the job pending outside worker slots. Independent
jobs can enter the pool even when earlier declarations await resources.
An idle resource wait is bounded by `--timeout`, restarting after a job completes;
time spent executing independent jobs does not consume the later idle wait.
The result-store wait and each command are also bounded by `--timeout`. Lock files remain stable
and are never unlinked on release.

Ownership spans input hashing, command execution, descendant process-group
teardown, output validation and atomic result publication. The existing bounded
WBS executor kills descendants on success, error and timeout before releasing
ownership. A small command supervisor inherits the lease descriptors and retains
them through cleanup if the owning runner dies. Killing that supervisor or an
entire host ends this cooperative lifetime guarantee. Missing or unsafe output
after command success is executor failure.
No Windows or real VM execution is claimed by the Linux CLI tests.

Derived policy consistency: mandatory source/artifact roots cannot be excluded
or denied. Reusable jobs share this static check with WBS and target issuance,
refuse denied descendants before opening their bytes, and enforce source and
artifact byte budgets during the existing streaming read. Policy errors and
budget overflow are unavailable verification, never command success or reusable
PASS. No second whole-source hash is required to enforce these limits.
Each input observation also stays within the invocation's bounded timeout;
this does not increase the declared source or artifact byte allowance.
Producer output observations apply that same timeout and the existing aggregate
artifact byte allowance, before reuse and after execution. Missing or invalid
old output may be rebuilt, but only valid final output can PASS. Reuse requires
the output manifest to remain unchanged through validation. Jobs with no declared
outputs retain an empty manifest. Measurement failure releases job ownership.

## Verification

`tests/test_verify_runner.py` retains legacy ordering/worktree behavior.
`tests/test_verify_runner_jobs.py` exercises actual Linux CLI preparation and
consumers, scoped reuse, restored views, lost/changed artifacts, invalid inputs,
cross-process named contention with independent progress, output-leaf locks,
capability/launch/command/timeout failures and descendant teardown. Normal
conftest runtime-removal guards remain enabled; use a task-local pytest basetemp.
