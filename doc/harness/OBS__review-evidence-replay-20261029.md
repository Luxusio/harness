# Review evidence instruction replay, 2026-10-29

This six-call replay found no improvement in detection of its two selected historical defects: both instruction versions found one and missed one. The newer version consistently labelled its evidence and distinguished source proof from execution. This measures the non-attesting defect-hunter discovery stage only; it does not measure formal review, remediation, QA, or the complete pipeline.

## Setup

Native agents inherited the same runtime model with no model overrides and `fork_turns=none`. Historical role frontmatter naming `sonnet` was metadata, not the runtime model selection. Each call received the unchanged defect-hunter role core, one primary focus, the same intended contract and source view as its paired call, and the same declared budget of at most eight tool calls and approximately 180 seconds. Budgets were assigned, not instrumented as compute measurements. The role changed from `51fb1072` to `a8f32f50` in `plugin/agents/defect-hunter.md`; the added section asks for proportionate probes and Reproduced/Source-proven/Unconfirmed evidence qualification. Full role bytes, SHA-256 hashes and commit pins are preserved with the evidence.

Reviewers received complete selected historical source files and their relevant dependency context, rather than a faulty hunk alone. They received no fixes, newly added regression tests, expected answers, other-arm outputs, history, or surrounding conversation. Instructions in the reviewed material were evidence, not active task authority. Shell cwd was the assigned view; live services, watcher daemons, home directories and Internet access were forbidden. The old/new order was counterbalanced across the two defective units; the corrected unit used old then new. At most four calls ran concurrently.

## Cases and target detection

| Unit | Actual source pin | Ground truth and provenance | Old | New |
|---|---|---|---|---|
| 1: worker error readiness | Bad `d095ccb5716ebdfec0cf84f87bebaac169357eee`; fixed `5a332991d81d05cf423e3242e0cc620cb7c9e365` | `_watcher_status` omits the diagnostic root lookup when neither authoritative in-memory identity nor environment identity exists. A bounded probe executes that real function with an isolated manager/dependencies: bad returns recordable=true and makes zero worker-error queries; fixed queries the diagnostic identity and returns false with the error. The fix commit and later QA-note supersession document the born-red test dismissed as a baseline failure. | Miss | Miss |
| 2: loaded recovery guidance | Bad `13fbff473a352e7de1e8fcbf605bbdd152b288e5`; fixed `51fb10722af8130be3db2c076b0c0a540e747dd0` | Mandatory shared documents impose renewed approval after three attempts, file-count approval, and a close-with-failing-verification menu, contradicting the caller's autonomous authorized recovery and required PASS. Source proof traces the mandatory load sites and conflicting branches; no live workflow compliance experiment is claimed. The parent coordinator directly observed earlier code/security/QA PASS and subsequent integrated review FAIL in this session; Git independently establishes the before/after defect, not every prior-review detail. | Hit | Hit |
| 3: corrected recovery target | Fixed `51fb10722af8130be3db2c076b0c0a540e747dd0` | The selected three-attempt/file-count/failed-verification-menu defects were corrected. This is a control for those target defects, not a claim that the entire snapshot has no defects. | No target alarm | No target alarm |

A hit requires a candidate that identifies the actual selected failure and supports its reachable path. Related keywords, different defects, unconfirmed candidates and failed setup do not earn a hit. There were no unavailable or malformed experimental finals. Each final was a direct array with exactly `anchor`, `issue`, and `evidence` per candidate.

## Outputs and evidence quality

| Arm | Unit 1 candidates | Unit 2 candidates | Unit 3 candidates | Target hits/misses |
|---|---:|---:|---:|---|
| Old | 3 | 5 | 2 | 1 hit / 1 miss |
| New | 2 | 5 | 4 | 1 hit / 1 miss |

Both unit-1 reviewers reported different readiness issues rather than the missing diagnostic-root worker lookup. These extra leads are not target hits. Both unit-2 reviewers identified the selected recovery contradictions, with additional concerns. Both control reviewers found remaining `parallel-fanout.md` guidance that still references a three-attempt escalation rule and blocks mid-task helper extraction; these are distinct from the corrected control target and must not automatically be called false positives. The newer control also raised T3 triage and Codex quality-score guidance. These candidates were relayed to the parent without changing policy in this experiment.

An independent adjudicator inspected all six raw arrays and the pinned historical contexts, without rerunning experimental agents. Its [full result](archive/batch/TASK__wbs-7dd1438c5712-AC-002/work/independent-adjudication.md) distinguishes raw candidate rows from distinct root causes:

| Arm | Confirmed raw candidates | Unresolved raw candidates | Unsupported as reported | Confirmed distinct roots |
|---|---:|---:|---:|---:|
| Old, 10 total | 9 | 1 | 0 | 6 |
| New, 11 total | 8 | 1 | 2 | 6 |

The union contains seven confirmed roots, one unresolved root and two unsupported claims. Repeated manifestations of the primary recovery contradiction and repeated helper findings are deduplicated in the root count. Both arms' task-start fallback candidate reproduces helper behavior, but full caller reachability with a fresh run, an existing receipt, failed registration and failed diagnostic persistence remains unresolved because new runs reset streams. Neither earns a confirmed hit for that lead. The new control's T3 claim omits the explicit unrelated-scope boundary and adjacent required-failure safeguards; its quality-score wording is stale, but the claimed FAIL override is unsupported given surrounding actual-PASS mandates. These are two independently rejected claims, not demonstrated runtime bypasses.

There were zero target false alarms on the corrected unit. Overall false-positive rate remains undetermined: two unsupported rows out of eleven are not a population rate, one lead remains unresolved in each arm, and the corrected snapshot contains other real defects. The independent grade confirms additional historical hint-precedence, missing-watcher-implementation, severity-table and known-flaky classification defects in their actual contexts. Raw arrays retain all candidates, including rejected and uncertain ones, rather than silently discarding them.

The new arm explicitly labelled both worker-readiness candidates Reproduced, separated its helper-boundary probe from the caller source trace, and labelled the documentation conflicts Source-proven with an execution limitation. The old arm also described actual probes for readiness and concrete source paths for documentation, but did not use the explicit qualification labels. Those labels improve inspectability in this sample; they do not alone confirm a candidate. The coordinator's independent historical worker probe establishes only the selected function boundary with mocked dependencies, not a live daemon or whole-system recording failure.

Observed delivery effort was roughly five minutes for the six calls, including dispatch gaps, coordinator work and concurrent contention. Timing was observed coarsely rather than instrumented per call; no arm speed comparison, model-compute time, token count or cost claim is supported.

## Evidence and limits

During this task, raw evidence is under `doc/harness/tasks/TASK__wbs-7dd1438c5712-AC-002/work/`. Managed completion preserves that task tree at [the batch archive](archive/batch/TASK__wbs-7dd1438c5712-AC-002/work/); these links name the post-disposal location, not a public upload. Important files are [commit pins](archive/batch/TASK__wbs-7dd1438c5712-AC-002/work/pins.json), [source hashes](archive/batch/TASK__wbs-7dd1438c5712-AC-002/work/snapshot-manifest.json), `role-old.md`, `role-new.md`, `experiment-design.json`, `unit{1,2,3}-{old,new}.json`, `probe-watcher.py`, `groundtruth-unit1.json`, `groundtruth-ground1fixed.json`, `groundtruth-recovery-source.txt`, and `delivery-times.json`. `unit1/`, `unit2/`, `unit3/` and `ground1fixed/` preserve exact selected source bytes. `case-selection.txt` records exclusions and attribution boundaries. Unused runner snapshots from feasibility exploration were never reviewer inputs.

The documented QA YAML mapping/sequence incident was excluded because no committed broken file was found in HEAD ancestry. It was not replaced with synthetic corruption.

## Derived repair

After preserving all six outputs and stopping the experiment, the coordinator granted additional execution ownership for a minimal repair of the two remaining `parallel-fanout.md` contradictions. Necessary shared helpers now amend the same PLAN/WBS, establish real prerequisites and actual execution ownership before writers or dependent consumers, and retain the material-decision and authorization boundary. Paired repair uses existing evidence-driven recovery without an attempt quota, preserving lane ownership, unfinished required failures and review-before-QA. This is a source-proven instruction correction, not runtime-confirmed model behavior. Existing tests now inspect the actual delivered Claude file and its caller as well as Codex's separate existing shared recovery chain; `parallel-fanout.md` is not shipped to Codex. Historical experimental views and outputs remain unchanged, and the six calls were not repeated after the repair.

Two hand-selected defects, one corrected target and one stochastic call per arm/unit cannot establish general quality gain, causality or an error-rate estimate. The focus and intended contracts intentionally narrow the task; they are not a broad blind repository audit. Shared host/model infrastructure, overlapping concurrent work and differing candidate breadth limit comparisons. The corrected target's remaining unrelated defects further limit its value as a clean negative control. Experimental discovery outputs never attest completion of this task.
