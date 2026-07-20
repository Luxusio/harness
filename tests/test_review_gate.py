from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("review_gate_lib", ROOT / "plugin/scripts/_lib.py")
assert SPEC and SPEC.loader
lib = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = lib
SPEC.loader.exec_module(lib)


def _task(tmp_path: Path, touched: list[str], project_type: str = "library") -> Path:
    (tmp_path / ".git").mkdir()
    manifest = tmp_path / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(f"type: {project_type}\nqa:\n  browser_qa_supported: false\n", encoding="utf-8")
    task = tmp_path / "doc/harness/tasks/TASK__review"
    task.mkdir(parents=True)
    touched_yaml = "[]" if not touched else "\n" + "\n".join(f"  - {path}" for path in touched)
    (task / "TASK_STATE.yaml").write_text(
        "task_id: TASK__review\nstatus: created\nruntime_verdict: pending\n"
        f"touched_paths: {touched_yaml}\nplan_session_state: closed\nclosed_at: null\nupdated: now\n",
        encoding="utf-8",
    )
    (task / "PLAN.md").write_text("# plan\n", encoding="utf-8")
    return task


def _record(
    task: Path,
    agent_type: str,
    agent_id: str,
    status: str,
    verdict: str = "",
    head_sha: str | None = None,
):
    receipt = {
        "source": "test_hook",
        "agent_type": agent_type,
        "agent_id": agent_id,
        "status": status,
        "verdict": verdict,
        "summary": (
            f"VERDICT: {verdict}\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0"
            if verdict else "started"
        ),
    }
    if head_sha is not None:
        receipt["head_sha"] = head_sha
    return lib.record_subagent_receipt(task, receipt)


def test_docs_only_task_has_explicit_review_exemption(tmp_path):
    task = _task(tmp_path, ["doc/designs/change.md"])
    assert lib.required_review_lenses(task) == []
    assert lib.receipt_review_verdict(task) == "NOT_APPLICABLE"


def test_executable_doc_sql_and_dependency_manifests_are_reviewed(tmp_path):
    sql = tmp_path / "doc/sql/migrate.sql"
    sql.parent.mkdir(parents=True)
    sql.write_text("DROP TABLE users;\n", encoding="utf-8")
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("unsafe-package==1.0\n", encoding="utf-8")
    task = _task(tmp_path, ["doc/sql/migrate.sql", "requirements.txt"])

    assert lib.required_review_lenses(task) == ["review-code", "review-security"]


def test_dependency_manifest_alone_routes_security_review(tmp_path):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("ordinary-package==1.0\n", encoding="utf-8")
    task = _task(tmp_path, ["requirements.txt"])
    assert lib.required_review_lenses(task) == ["review-code", "review-security"]

    variant_root = tmp_path / "variant"
    variant_root.mkdir()
    variant = variant_root / "requirements-prod.txt"
    variant.write_text("ordinary-package==2.0\n", encoding="utf-8")
    task = _task(variant_root, ["requirements-prod.txt"])
    assert lib.required_review_lenses(task) == ["review-code", "review-security"]


def test_admin_and_role_changes_route_security_review(tmp_path):
    handler = tmp_path / "src/handler.py"
    handler.parent.mkdir(parents=True)
    handler.write_text("def allowed(user):\n    return user.is_admin\n", encoding="utf-8")
    task = _task(tmp_path, ["src/handler.py"])
    assert lib.required_review_lenses(task) == ["review-code", "review-security"]


def test_source_always_routes_code_and_security_uses_path_or_content(tmp_path):
    generic = tmp_path / "src/handler.py"
    generic.parent.mkdir(parents=True)
    generic.write_text("def run(value):\n    return value\n", encoding="utf-8")
    task = _task(tmp_path, ["src/handler.py"])
    assert lib.required_review_lenses(task) == ["review-code"]

    generic.write_text("def run(user_token):\n    return user_token\n", encoding="utf-8")
    assert lib.required_review_lenses(task) == ["review-code", "review-security"]


def test_security_router_scans_large_files_and_common_tls_configuration(tmp_path):
    generic = tmp_path / "src/client.py"
    generic.parent.mkdir(parents=True)
    generic.write_text(("# padding\n" * 25000) + "verify=False\n", encoding="utf-8")
    task = _task(tmp_path, ["src/client.py"])
    assert lib.required_review_lenses(task) == ["review-code", "review-security"]


def test_verdict_must_be_unique_and_exactly_on_first_line():
    assert lib.extract_qa_verdict("VERDICT: PASS\n12 tests passed") == "PASS"
    assert lib.extract_qa_verdict("preamble\nVERDICT: PASS") == ""
    assert lib.extract_qa_verdict("**VERDICT: PASS**") == ""
    assert lib.extract_qa_verdict("VERDICT: PASS\nVERDICT: FAIL") == ""


def test_review_completion_without_structured_finding_counts_stays_pending(tmp_path):
    source = tmp_path / "src/main.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    task = _task(tmp_path, ["src/main.py"])
    _record(task, "harness:code-reviewer", "review", "started")
    lib.record_subagent_receipt(task, {
        "source": "test_hook", "agent_type": "harness:code-reviewer",
        "agent_id": "review", "status": "completed", "verdict": "PASS",
        "summary": "VERDICT: PASS",
    })
    assert lib.receipt_review_verdict(task) == "PENDING"


def test_review_verdict_and_finding_counts_must_be_consistent(tmp_path):
    source = tmp_path / "src/main.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    task = _task(tmp_path, ["src/main.py"])
    _record(task, "harness:code-reviewer", "review", "started")
    entry = lib.record_subagent_receipt(task, {
        "source": "test_hook", "agent_type": "harness:code-reviewer",
        "agent_id": "review", "status": "completed", "verdict": "PASS",
        "summary": "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=1 INVESTIGATE=0 OPTIONAL=0",
    })
    assert entry["verdict"] == "PENDING"

    entry = lib.record_subagent_receipt(task, {
        "source": "test_hook", "agent_type": "harness:code-reviewer",
        "agent_id": "review-conflict", "status": "completed", "verdict": "PASS",
        "summary": (
            "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n"
            "FINDING_COUNTS: FIX_NOW=1 INVESTIGATE=0 OPTIONAL=0"
        ),
    })
    assert entry["verdict"] == "PENDING"

    for verdict, counts in (
        ("FAIL", "FIX_NOW=0 INVESTIGATE=0 OPTIONAL=1"),
        ("BLOCKED_ENV", "FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0"),
    ):
        entry = lib.record_subagent_receipt(task, {
            "source": "test_hook", "agent_type": "harness:code-reviewer",
            "agent_id": f"review-{verdict}", "status": "completed", "verdict": verdict,
            "summary": f"VERDICT: {verdict}\nFINDING_COUNTS: {counts}",
        })
        assert entry["verdict"] == "PENDING"

    entry = lib.record_subagent_receipt(task, {
        "source": "test_hook", "agent_type": "harness:code-reviewer",
        "agent_id": "review-third-line", "status": "completed", "verdict": "PASS",
        "summary": "VERDICT: PASS\npreamble\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0",
    })
    assert entry["verdict"] == "PENDING"


def test_review_requires_correlated_start_and_current_diff(tmp_path):
    source = tmp_path / "src/main.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    task = _task(tmp_path, ["src/main.py"])

    _record(task, "harness:code-reviewer", "review-1", "completed", "PASS")
    assert lib.receipt_review_verdict(task) == "PENDING"

    _record(task, "harness:code-reviewer", "review-2", "started")
    source.write_text("VALUE = 2\n", encoding="utf-8")
    _record(task, "harness:code-reviewer", "review-2", "completed", "PASS")
    assert lib.receipt_review_verdict(task) == "PENDING"

    _record(task, "harness:code-reviewer", "review-3", "started")
    _record(task, "harness:code-reviewer", "review-3", "completed", "PASS")
    assert lib.receipt_review_verdict(task) == "PASS"

    completed = lib.list_review_receipts(task)[-1]
    assert completed["event"] == "review_completed"
    assert completed["base_sha"] == completed["head_sha"]
    assert completed["finished_at"]
    assert completed["finding_counts"] == {"fix_now": 0, "investigate": 0, "optional": 0}


def test_review_requires_correlated_and_current_head(tmp_path):
    source = tmp_path / "src/main.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    task = _task(tmp_path, ["src/main.py"])

    _record(task, "harness:code-reviewer", "review-old", "started", head_sha="old-head")
    _record(task, "harness:code-reviewer", "review-old", "completed", "PASS", head_sha="old-head")
    assert lib.receipt_review_verdict(task) == "PENDING"

    _record(task, "harness:code-reviewer", "review-mixed", "started", head_sha="old-head")
    _record(task, "harness:code-reviewer", "review-mixed", "completed", "PASS")
    assert lib.receipt_review_verdict(task) == "PENDING"

    _record(task, "harness:code-reviewer", "review-current", "started")
    _record(task, "harness:code-reviewer", "review-current", "completed", "PASS")
    assert lib.receipt_review_verdict(task) == "PASS"


def test_security_lens_is_separate_and_required_when_routed(tmp_path):
    source = tmp_path / "src/auth.py"
    source.parent.mkdir(parents=True)
    source.write_text("def authorize():\n    return True\n", encoding="utf-8")
    task = _task(tmp_path, ["src/auth.py"])
    _record(task, "harness:code-reviewer", "code", "started")
    _record(task, "harness:code-reviewer", "code", "completed", "PASS")
    assert lib.receipt_review_verdict(task) == "PENDING"
    _record(task, "harness:security-reviewer", "security", "started")
    _record(task, "harness:security-reviewer", "security", "completed", "PASS")
    assert lib.receipt_review_verdict(task) == "PASS"


def test_qa_must_start_after_latest_review_pass(tmp_path):
    source = tmp_path / "src/main.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    task = _task(tmp_path, ["src/main.py"])

    _record(task, "harness:qa-cli", "qa-old", "started")
    _record(task, "harness:qa-cli", "qa-old", "completed", "PASS")
    _record(task, "harness:code-reviewer", "review", "started")
    _record(task, "harness:code-reviewer", "review", "completed", "PASS")
    assert lib.receipt_runtime_verdict(task) == "PENDING"

    _record(task, "harness:qa-cli", "qa-new", "started")
    _record(task, "harness:qa-cli", "qa-new", "completed", "PASS")
    assert lib.receipt_runtime_verdict(task) == "PASS"
