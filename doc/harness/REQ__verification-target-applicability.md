# Verification target applicability

## User requirements
Users request reusable review and QA evidence bound to actual inputs, relevant-change invalidation, preserved authentic lifecycle history, diagnostic experiments without product acceptance inflation, and no repeated checks solely to repair recording. Restoring identical source at another path must preserve applicability. Unrelated documentation edits must not invalidate product QA.

## Derived mechanism
Before new Codex review/QA, the owner calls `wbs(action="target", wbs_id=..., target=...)`. The specification declares explicit WBS `inputs`, `commands` as argv arrays, `lenses`, and `purpose` (`acceptance` or `diagnostic`), with optional environment key names and toolchain executable names. Actual platform, toolchain binary contents and hashes of selected environment values join the copied input manifest. Build flags and dependency/configuration files belong in these actual verification inputs, not an edited-file progress plan. Independent review checks scope adequacy.

The response supplies a random token and persistent `snapshot_root`. Append `_vt_<token>` to the structured native agent task_name and direct that role to read and execute against snapshot_root. Source files are copied without hardlinks and made read-only; source checkout changes cannot change that view. Runtime command output belongs outside selected inputs. Optional `output_dirs` prepare bounded writable runtime-only directories (for example `doc/harness/tasks` for the existing pytest session guard). Every selected input file remains read-only; output paths cannot overlap or be ancestors of selected inputs, and cannot be inside a selected source root unless explicitly excluded. These output paths are not target identity. Commands and explicit snapshot roots must be passed to execution tools; repository autodiscovery may walk outside a copied view and must not select mutable source. The snapshot is a declared controlled role view, not an OS sandbox or proof of every arbitrary tool read; same-UID hostile owners are outside this protection.

## Observable behavior
- An issued snapshot remains A while the checkout changes A → B → A. Gates compare current actual inputs with copied A, not a source hash observed during delayed receipt replay.
- Existing native receipt source/task/run/agent/type/runtime identity authenticates the declared token. The matched first start/completion for an exact native agent is supported. Followups inherit names and remain source-unbound; issue a target and spawn a fresh agent for changed targets.
- Receipt schemas and lifecycle parsing/order remain unchanged. Direct finals, copied text, diagnostics and historical unbound PASS never create target-certified acceptance.
- `context`, open and plan do not hash inputs. Explicit verify, close and verified installation enforce active per-lens applicability separately from lifecycle verdicts. Relevant changes block target-aware completion and preserve old evidence. Completed results retain manifest-bound applicability if regenerable copied views disappear; a new job reconstructs only from identical current bytes.
- Identical source content restored elsewhere is equivalent. Unselected documentation does not stale product results; selected source/dependencies/build context do. Changed command scope requires a new acceptance target, while identical issuance reuses its frozen view.
- Diagnostic targets may declare an empty lenses list for direct runner experiments; they do not replace acceptance targets or satisfy product completion. They can supply bounded investigation evidence without declaring review/QA acceptance.
- Existing lifecycle-only tasks are visibly `unbound`, retain their old lifecycle semantics and are never retroactively source-certified. Issuing an acceptance target enables future required-lens applicability for that task. Current native token association supports Codex; unbound runtime identities are reported rather than fabricated.
- Full target manifests and historical copied inputs persist under task work storage. WBS keeps a small active lens index; old target history is not appended into PLAN or WBS. No lifetime 64-target cap, no new scheduler.

Derived from preserving asynchronous receipt delivery: final explicit verify
measures target input bytes and execution context outside the receipt-writer
transaction. Final publication takes a fresh receipt snapshot under that
transaction and associates its authentic tokens with the measured input proof.
The task run, control, WBS and active target index must still match; stale or
unavailable inputs and real negative verdicts still prevent acceptance. A source-phase
completion must be able to append and be considered by that same verify call.
The measurement is private in-memory state, not reusable external acceptance
evidence; close and verified installation retain their fresh applicability gates.

## Input limits and errors
Canonical WBS policy and streaming measurement budgets apply; required files are not removed to obtain PASS. Unsupported symlink/nonregular inputs, missing dependencies, unsafe ownership/modes, nested checkout roots and recursive task/runtime input scopes fail explicitly. Select concrete nested-module source inputs instead of silently omitting them. Target metadata is bounded to 1 MiB per immutable record; refine actual validation scope if that bound is exceeded. Source and artifact selection must match declared policy. Private copied data can be read-only, but Harness does not intercept arbitrary shell mutations.

Derived from preserving actual source inputs: owner-private readable source
such as0600/0640 is valid without changing its original permissions. The existing
descriptor reader retains ownership, single-link, no-follow and stability checks;
source inputs reject group/other write, special permission bits, unreadable owner
modes and executable bits without owner-execute. Copies remain private and
read-only. This source-read policy does not relax canonical installer payload or
frozen-copy permission checks.

## Verification
Actual source-copy execution under checkout ABA; content restoration and documentation-only reuse; native first-generation versus followup association; changed build options; diagnostic isolation; missing snapshot; public target action and stored-only context; authoritative verify/close/install applicability. All tests preserve independent lifecycle receipt authenticity and ordering.
