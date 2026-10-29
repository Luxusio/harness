# REQ — Harness activation belongs to initialized projects

## User requirement

On 2026-10-29 the user explicitly required: “하네스 설치 안 된 폴더에서는 아얘 안 동작하게 되나? 그래야하는데. 설치된 폴더에서만 동작해야해”. Globally installed hooks and tools must not activate the project workflow merely because they are available to the editor.

## Observable contract

- A project is initialized by explicit `harness:setup`, represented by its regular, non-symlink `doc/harness/manifest.yaml`. The control directories must also be real directories. Git alone, an active task binding, or a global plugin installation does not initialize a project.
- Hooks resolve the project from the supplied valid `cwd`. An explicitly invalid or nonexistent `cwd`, or rejected/truncated hook input, must not fall back to the hook process directory. A valid payload without `cwd` uses the process directory.
- Initialization covers ordinary subdirectories, including projects without Git. A nested Git checkout is an installation boundary and needs its own manifest, even when the outer project has an active task. A directory alias to an initialized project remains valid; symlinked manifest/control components do not grant authority.
- In an uninitialized project, registered Claude and Codex hooks return silently without project workflow output, task focus binding, or project metadata creation. In particular there is no welcome banner, routing reminder, task context, task/runtime/archive directory, or learning/checkpoint output.
- Public WBS requests, including open and migration, refuse before project metadata effects when initialization is absent. The response directs the caller to explicit setup; it never initializes a project automatically.
- The run skill applies only inside this boundary. Ordinary development outside it continues without entering Harness.

## Existing protection retained

Native Codex session metadata remains runtime-owned under C-05 and the receipt-free execution contract, including when the current project is uninitialized. This host ownership guard is separate from project workflow activation. Invalid initialized control metadata also retains its existing fail-closed write protection. No receipt collector or new registry is introduced.

## Provenance and interpretation

| Rule | Source |
| --- | --- |
| Activate only in installed project folders | Explicit user request above |
| Manifest authority, nested Git boundary, payload cwd, refusal before effects | Implementation interpretation making that request testable, rather than additional product requirements attributed to the user |
| Native host metadata ownership and invalid-control protection | Existing C-05 and receipt-free execution contract |

This extends the 2026-05-31 hook no-op requirement to the actual registered hooks, public WBS entry and skill routing. The old exception allowing MCP writes outside initialized projects is retired.

## Verification

`tests/test_non_harness_hooks_noop.py`, `tests/test_wbs_project_activation.py`, the Codex wrapper tests and task-control tests exercise real hook entry points and public stdio WBS calls. They check silent/no-write behavior, explicit cwd isolation, initialized positive cases, nested Git isolation, invalid control paths and retained native ownership protection. Session-start tests execute the registered banner command rather than assuming registration implies activation.
