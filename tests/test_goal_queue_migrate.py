import json
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugin" / "scripts" / "goal_queue_migrate.py"


def run_migrate(tmp_path, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(tmp_path), *args],
        text=True,
        capture_output=True,
        timeout=20,
    )


def test_migrates_legacy_state_and_archives_source(tmp_path):
    harness = tmp_path / "doc" / "harness"
    harness.mkdir(parents=True)
    legacy = harness / "autopilot.yaml"
    legacy.write_text(
        json.dumps(
            {
                "version": 1,
                "status": "active",
                "slices": [
                    {
                        "id": "auth",
                        "title": "Login",
                        "task_id": "TASK__autopilot-auth",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_migrate(tmp_path, "--state-only")
    assert result.returncode == 0, result.stderr
    assert "state: migrated to doc/harness/goal-queue.json" in result.stdout

    target = harness / "goal-queue.json"
    state = json.loads(target.read_text(encoding="utf-8"))
    assert state["migrated_from"] == "doc/harness/autopilot.yaml"
    assert state["slices"][0]["task_id"] == "TASK__goal-queue-auth"
    assert state["slices"][0]["legacy_task_id"] == "TASK__autopilot-auth"
    assert state["task_id_migration"]["rewritten_missing_task_dir"] == 1
    assert not legacy.exists()
    archives = list((harness / "legacy").glob("goal-queue-pre-native-state.*.json"))
    assert len(archives) == 1


def test_preserves_legacy_task_id_when_task_dir_exists(tmp_path):
    harness = tmp_path / "doc" / "harness"
    harness.mkdir(parents=True)
    task_dir = harness / "tasks" / "TASK__autopilot-auth"
    task_dir.mkdir(parents=True)
    (task_dir / "TASK.json").write_text(json.dumps({
        "run_id": "0198c349-5800-7000-8000-000000000001",
        "execution_mode": "standard",
        "required_lenses": ["review-code", "qa-cli"],
        "close_receipt_fingerprint": None,
    }) + "\n", encoding="utf-8")
    (harness / "autopilot.yaml").write_text(
        json.dumps(
            {
                "status": "active",
                "slices": [{"id": "auth", "task_id": "TASK__autopilot-auth"}],
            }
        ),
        encoding="utf-8",
    )

    result = run_migrate(tmp_path, "--state-only")
    assert result.returncode == 0, result.stderr

    state = json.loads((harness / "goal-queue.json").read_text(encoding="utf-8"))
    assert state["slices"][0]["task_id"] == "TASK__autopilot-auth"
    assert state["slices"][0]["legacy_task_id"] == "TASK__autopilot-auth"
    assert state["task_id_migration"]["preserved_existing_task_dir"] == 1
    assert task_dir.is_dir()


def test_state_migration_is_idempotent_when_target_exists(tmp_path):
    harness = tmp_path / "doc" / "harness"
    harness.mkdir(parents=True)
    (harness / "autopilot.yaml").write_text(json.dumps({"slices": []}), encoding="utf-8")
    target = harness / "goal-queue.json"
    target.write_text(json.dumps({"status": "active", "slices": []}), encoding="utf-8")

    result = run_migrate(tmp_path, "--state-only")
    assert result.returncode == 0
    assert "state: goal-queue state already exists" in result.stdout
    assert json.loads(target.read_text(encoding="utf-8")) == {"status": "active", "slices": []}
    assert (harness / "autopilot.yaml").exists()


def test_updates_marked_claude_routing_block_and_legacy_agent_line(tmp_path):
    claude = tmp_path / "CLAUDE.md"
    claude.write_text(
        "\n".join(
            [
                "# Project",
                "- Default agent is harness",
                "",
                "## Harness routing",
                "<!-- harness:routing-injected -->",
                "- Run the full cycle -> choose run or autopilot",
                "",
                "## Local Notes",
                "Keep me.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = run_migrate(tmp_path, "--routing-only")
    assert result.returncode == 0, result.stderr
    assert "routing: updated CLAUDE.md" in result.stdout

    body = claude.read_text(encoding="utf-8")
    assert "Default agent is harness" not in body
    assert "choose run or autopilot" not in body
    assert "$harness:run" in body
    assert "invoke `$harness:run` before editing" in body
    assert "## Local Notes\nKeep me." in body


def test_routing_migration_is_idempotent(tmp_path):
    claude = tmp_path / "CLAUDE.md"
    first = run_migrate(tmp_path, "--routing-only")
    assert first.returncode == 0
    assert "routing: CLAUDE.md absent" in first.stdout

    claude.write_text("# Project\n", encoding="utf-8")
    second = run_migrate(tmp_path, "--routing-only")
    assert second.returncode == 0
    current = claude.read_text(encoding="utf-8")

    third = run_migrate(tmp_path, "--routing-only")
    assert third.returncode == 0
    assert "routing: already current" in third.stdout
    assert claude.read_text(encoding="utf-8") == current


def test_routing_migration_supports_codex_agents_md(tmp_path):
    agents = tmp_path / "AGENTS.md"
    agents.write_text("# Project\n", encoding="utf-8")

    result = run_migrate(tmp_path, "--routing-only", "--project-doc", "AGENTS.md")

    assert result.returncode == 0
    assert "routing: updated AGENTS.md" in result.stdout
    assert "<!-- harness:routing-injected -->" in agents.read_text(encoding="utf-8")
    assert not (tmp_path / "CLAUDE.md").exists()


def test_setup_docs_reference_existing_repo_migration():
    bootstrap = (REPO / "plugin" / "skills" / "setup" / "bootstrap.md").read_text(encoding="utf-8")
    setup = (REPO / "plugin" / "skills" / "setup" / "SKILL.md").read_text(encoding="utf-8")
    native_goals = (REPO / "doc" / "harness" / "patterns" / "native-goals.md").read_text(encoding="utf-8")

    assert "goal_queue_migrate.py" in bootstrap
    assert "doc/harness/autopilot.yaml" in bootstrap
    assert "doc/harness/goal-queue.json" in bootstrap
    assert "setup_finalize.py" in bootstrap
    assert "already present — skipping" not in bootstrap
    assert "idempotent replace/append" in bootstrap
    assert "Goal queue migration from §3.4" in setup
    assert "Repair/Upgrade setup runs `plugin/scripts/goal_queue_migrate.py`" in native_goals
