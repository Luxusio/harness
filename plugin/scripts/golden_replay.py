#!/usr/bin/env python3
"""Golden replay regression tests for harness scripts.

Stdlib only. Runs a fixed set of known-good inputs through the harness
scripts and compares outputs against expected snapshots. Exit 0 on
all-pass, 1 on any regression.

Covered today:
   1. contract_lint.py — the shipped template must lint clean.
   2. note_freshness.py — current → suspect flip on path match.
   3. contract_lint.py --check-weight — flags over-budget SKILL.md files.
   4. prewrite_gate.py — emits JSON permissionDecision=deny on protected artifact.
   5. mcp_bash_guard.py — emits JSON permissionDecision=deny on `sed -i` into workflow-control-surface.
   6. harness_server.task_close — blocks when runtime verification is missing.
   7. prompt_memory.py — emits compact current task/verdict context.
   8. tool_routing.py — emits [harness-hint] on `command not found: pytest`.

Invoke:
  python3 plugin/scripts/golden_replay.py           # all tests
  python3 plugin/scripts/golden_replay.py -v        # verbose
  python3 plugin/scripts/golden_replay.py --only prompt_memory  # single

Used by: CI / pre-release smoke / manual regression check after script
edits. Never invoked from a hook (slow, writes tmp files).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
TEMPLATES = os.path.join(ROOT, "skills", "setup", "templates")


class TestResult:
    def __init__(self, name: str, ok: bool, msg: str = ""):
        self.name = name
        self.ok = ok
        self.msg = msg

    def __str__(self) -> str:
        tag = "PASS" if self.ok else "FAIL"
        return f"[{tag}] {self.name}" + (f" — {self.msg}" if self.msg else "")


def _run(cmd: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=30)


def test_contract_lint_template() -> TestResult:
    """Shipped CONTRACTS.md template must lint clean."""
    tmpl = os.path.join(TEMPLATES, "CONTRACTS.md")
    if not os.path.isfile(tmpl):
        return TestResult("contract_lint_template", False, f"template missing: {tmpl}")
    r = _run(["python3", os.path.join(SCRIPTS, "contract_lint.py"),
              "--path", tmpl, "--repo-root", ROOT])
    if r.returncode != 0:
        return TestResult("contract_lint_template", False,
                          f"exit={r.returncode} stderr={r.stderr.strip()[:200]}")
    return TestResult("contract_lint_template", True)


def test_note_freshness_flip() -> TestResult:
    """Note with matching invalidated_by_paths flips current -> suspect."""
    with tempfile.TemporaryDirectory() as td:
        note_dir = os.path.join(td, "doc")
        os.makedirs(note_dir)
        note = os.path.join(note_dir, "example.md")
        with open(note, "w") as f:
            f.write(
                "---\n"
                "freshness: current\n"
                "invalidated_by_paths:\n"
                "  - src/changed.py\n"
                "---\n"
                "body\n"
            )

        r = _run(["python3", os.path.join(SCRIPTS, "note_freshness.py"),
                  "--paths", "src/changed.py",
                  "--doc-root", note_dir, "--quiet"])
        if r.returncode != 0:
            return TestResult("note_freshness_flip", False,
                              f"exit={r.returncode} stderr={r.stderr.strip()[:200]}")
        body = open(note).read()
        if "freshness: suspect" not in body:
            return TestResult("note_freshness_flip", False,
                              "freshness did not flip to 'suspect'")

    return TestResult("note_freshness_flip", True)


def test_check_weight_flags_oversized() -> TestResult:
    """--check-weight should flag at least one SKILL.md >500 lines when any exist."""
    with tempfile.TemporaryDirectory() as td:
        fake_plugin = os.path.join(td, "plugin")
        os.makedirs(os.path.join(fake_plugin, "skills", "big"))
        skill = os.path.join(fake_plugin, "skills", "big", "SKILL.md")
        with open(skill, "w") as f:
            f.write("\n".join(f"line {i}" for i in range(600)))

        # Need a CONTRACTS.md for lint to run at all — use shipped template.
        tmpl = os.path.join(TEMPLATES, "CONTRACTS.md")
        r = _run(["python3", os.path.join(SCRIPTS, "contract_lint.py"),
                  "--path", tmpl, "--repo-root", ROOT,
                  "--check-weight", "--plugin-root", fake_plugin])
        if r.returncode != 0:
            return TestResult("check_weight_flags_oversized", False,
                              f"exit={r.returncode} stderr={r.stderr.strip()[:200]}")
        if "C-13 weight" not in r.stdout:
            return TestResult("check_weight_flags_oversized", False,
                              f"C-13 weight warning missing from stdout:\n{r.stdout}")

    return TestResult("check_weight_flags_oversized", True)


def _repo_root() -> str:
    # ROOT in this file is plugin/ (golden_replay sits in plugin/scripts/).
    # Walk up one more level for the real git repo root.
    return os.path.dirname(ROOT)


def _invoke_hook(script_path: str, payload: dict) -> subprocess.CompletedProcess:
    import json
    repo = _repo_root()
    env = os.environ.copy()
    # Dual-name during v2.3 -> v2.5 deprecation window (AC-006 of dual-runtime v1).
    env["HARNESS_PLUGIN_ROOT"] = ROOT
    env["CLAUDE_PLUGIN_ROOT"] = ROOT  # deprecated; drop in v2.5
    return subprocess.run(
        ["python3", script_path],
        input=json.dumps(payload),
        capture_output=True, text=True, cwd=repo, env=env, timeout=5,
    )


def _parse_decision(stdout: str):
    import json
    if not stdout.strip():
        return None, None
    try:
        data = json.loads(stdout)
        hso = data.get("hookSpecificOutput") or {}
        return hso.get("permissionDecision"), hso.get("permissionDecisionReason")
    except Exception:
        return None, None


def test_prewrite_json_deny_on_protected_artifact() -> TestResult:
    """prewrite_gate emits JSON deny (not exit 2) on protected artifact write."""
    import shutil
    gate = os.path.join(SCRIPTS, "prewrite_gate.py")
    tasks_root = os.path.join(_repo_root(), "doc", "harness", "tasks")
    scratch = os.path.join(tasks_root, "TASK__golden-replay-prewrite")
    active = os.path.join(tasks_root, ".active")
    prev_active = None
    if os.path.isfile(active):
        with open(active) as f:
            prev_active = f.read()
    os.makedirs(scratch, exist_ok=True)
    try:
        with open(os.path.join(scratch, "PLAN.md"), "w") as f:
            f.write("# plan\n")
        with open(active, "w") as f:
            f.write(scratch)
        plan = os.path.join(scratch, "PLAN.md")
        r = _invoke_hook(gate, {"tool_name": "Write", "tool_input": {"file_path": plan}})
        if r.returncode != 0:
            return TestResult("prewrite_json_deny", False,
                              f"expected exit 0 (|| true compatibility), got {r.returncode}")
        decision, reason = _parse_decision(r.stdout)
        if decision != "deny":
            return TestResult("prewrite_json_deny", False,
                              f"decision={decision!r} (expected deny); stdout={r.stdout[:200]!r}")
        if reason is None or "C-05-protected-artifact" not in reason:
            return TestResult("prewrite_json_deny", False,
                              f"reason missing rule id; reason={reason!r}")
        if "HARNESS_SKIP_PREWRITE" not in reason:
            return TestResult("prewrite_json_deny", False,
                              "reason missing escape hint")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
        if prev_active is not None:
            with open(active, "w") as f:
                f.write(prev_active)
        else:
            try:
                os.unlink(active)
            except OSError:
                pass
    return TestResult("prewrite_json_deny", True)


def test_bash_guard_deny_on_sed_into_workflow_control() -> TestResult:
    """mcp_bash_guard emits JSON deny on `sed -i` targeting a workflow-control-surface file."""
    guard = os.path.join(SCRIPTS, "mcp_bash_guard.py")
    cmd = "sed -i 's/a/b/' plugin/hooks/hooks.json"
    r = _invoke_hook(guard, {"tool_name": "Bash", "tool_input": {"command": cmd}})
    if r.returncode != 0:
        return TestResult("bash_guard_deny", False,
                          f"expected exit 0, got {r.returncode}")
    decision, reason = _parse_decision(r.stdout)
    if decision != "deny":
        return TestResult("bash_guard_deny", False,
                          f"decision={decision!r}; stdout={r.stdout[:200]!r}")
    if reason is None or "rule=workflow-control-surface" not in reason:
        return TestResult("bash_guard_deny", False,
                          f"reason missing rule=workflow-control-surface; reason={reason!r}")
    if "HARNESS_SKIP_MCP_GUARD" not in reason:
        return TestResult("bash_guard_deny", False, "reason missing escape hint")
    return TestResult("bash_guard_deny", True)


def _load_mcp_server():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "harness_server",
        os.path.join(_repo_root(), "plugin", "mcp", "harness_server.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_prompt_memory_emits_context_block() -> TestResult:
    """prompt_memory.py emits compact current task and verdict context."""
    import os as _os
    import time as _time
    prompt = _os.path.join(SCRIPTS, "prompt_memory.py")
    with tempfile.TemporaryDirectory() as tmp:
        base = tmp
        _os.makedirs(_os.path.join(base, ".git"))
        tasks = _os.path.join(base, "doc", "harness", "tasks")
        _os.makedirs(tasks)
        with open(_os.path.join(base, "doc", "harness", "manifest.yaml"), "w") as f:
            f.write('test_command: "python3 -m pytest"\n')
        task_dir = _os.path.join(tasks, "TASK__gr-pr3")
        _os.makedirs(task_dir)
        with open(_os.path.join(task_dir, "PLAN.md"), "w") as f:
            f.write("# plan\n")
        with open(_os.path.join(task_dir, "TASK.json"), "w") as f:
            json.dump({
                "run_id": "01890a5d-ac96-774b-bcce-b3020998df21",
                "execution_mode": "standard",
                "required_lenses": ["review-code", "qa-cli"],
                "close_receipt_fingerprint": None,
            }, f)
            f.write("\n")
        _os.makedirs(_os.path.join(base, "src"))
        src = _os.path.join(base, "src", "foo.py")
        with open(src, "w") as f:
            f.write("pass\n")
        now = _time.time()
        _os.utime(src, (now, now))
        with open(_os.path.join(tasks, ".active"), "w") as f:
            f.write(task_dir)

        env = _os.environ.copy()
        # Dual-name env (AC-006). Drop CLAUDE_PLUGIN_ROOT in v2.5 per CHANGELOG.
        env["HARNESS_PLUGIN_ROOT"] = _os.path.join(ROOT, "plugin")
        env["CLAUDE_PLUGIN_ROOT"] = _os.path.join(ROOT, "plugin")
        r = subprocess.run(
            ["python3", prompt],
            input='{"prompt":"status"}',
            capture_output=True, text=True, cwd=base, env=env, timeout=5,
        )
    if r.returncode != 0:
        return TestResult("prompt_memory_context_block", False,
                          f"exit {r.returncode}: {r.stderr[:200]}")
    out = r.stdout
    for needle in ("[harness-context]", "task=TASK__gr-pr3", "verdict=PENDING"):
        if needle not in out:
            return TestResult("prompt_memory_context_block", False,
                              f"missing {needle!r} in stdout: {out!r}")
    if "doc/common/sus.md" in out:
        return TestResult("prompt_memory_context_block", False,
                          f"suspect note unexpectedly injected: {out!r}")
    if len(out) > 400:
        return TestResult("prompt_memory_context_block", False,
                          f"output exceeded 400 chars: {len(out)}")
    return TestResult("prompt_memory_context_block", True)


def test_tool_routing_suggests_test_command() -> TestResult:
    """tool_routing.py emits [harness-hint] citing manifest test_command on pytest-not-found."""
    import json as _json
    import os as _os
    routing = _os.path.join(SCRIPTS, "tool_routing.py")
    with tempfile.TemporaryDirectory() as tmp:
        _os.makedirs(_os.path.join(tmp, ".git"))
        _os.makedirs(_os.path.join(tmp, "doc", "harness"))
        with open(_os.path.join(tmp, "doc", "harness", "manifest.yaml"), "w") as f:
            f.write('test_command: "python3 -m pytest"\n')
        payload = {
            "tool_name": "Bash",
            "tool_response": {"stderr": "bash: pytest: command not found"},
        }
        env = _os.environ.copy()
        # Dual-name env (AC-006). Drop CLAUDE_PLUGIN_ROOT in v2.5 per CHANGELOG.
        env["HARNESS_PLUGIN_ROOT"] = _os.path.join(ROOT, "plugin")
        env["CLAUDE_PLUGIN_ROOT"] = _os.path.join(ROOT, "plugin")
        r = subprocess.run(
            ["python3", routing], input=_json.dumps(payload),
            capture_output=True, text=True, cwd=tmp, env=env, timeout=5,
        )
    if r.returncode != 0:
        return TestResult("tool_routing_suggests_test_command", False,
                          f"exit={r.returncode} stderr={r.stderr[:200]}")
    if "[harness-hint]" not in r.stdout:
        return TestResult("tool_routing_suggests_test_command", False,
                          f"hint prefix missing: {r.stdout!r}")
    if "python3 -m pytest" not in r.stdout:
        return TestResult("tool_routing_suggests_test_command", False,
                          f"test_command not cited: {r.stdout!r}")
    return TestResult("tool_routing_suggests_test_command", True)


TESTS = [
    test_contract_lint_template,
    test_note_freshness_flip,
    test_check_weight_flags_oversized,
    test_prewrite_json_deny_on_protected_artifact,
    test_bash_guard_deny_on_sed_into_workflow_control,
    test_prompt_memory_emits_context_block,
    test_tool_routing_suggests_test_command,
]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--only", help="Run only the test matching this substring")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON summary")
    args = p.parse_args()

    results = []
    for fn in TESTS:
        if args.only and args.only not in fn.__name__:
            continue
        try:
            res = fn()
        except Exception as e:
            res = TestResult(fn.__name__, False, f"exception: {e}")
        results.append(res)
        if not args.json and (args.verbose or not res.ok):
            print(res)

    passed = sum(1 for r in results if r.ok)
    total = len(results)
    if args.json:
        print(json.dumps({
            "passed": passed,
            "total": total,
            "ok": passed == total,
            "results": [
                {"name": r.name, "ok": r.ok, "message": r.msg}
                for r in results
            ],
        }, indent=2, sort_keys=True))
    else:
        print(f"\ngolden_replay: {passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
