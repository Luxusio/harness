"""Change context through the installed hook entry points and local feed."""
import fcntl
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugin/scripts"
EPOCH = "1" * 32


@pytest.fixture
def setup_wiki(tmp_path):
    repo = tmp_path / "project"
    (repo / "doc/harness").mkdir(parents=True)
    (repo / "doc/harness/manifest.yaml").write_text("type: library\n")
    (repo / ".git").mkdir()
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("HARNESS_", "CLAUDE_", "CODEX_", "WIKI_", "ATLASSIAN_"))}
    # Hooks intentionally execute python3; declare the parser-capable fixture interpreter.
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env.update(
        PYTHONDONTWRITEBYTECODE="1",
        HOME=str(tmp_path / "home"),
        XDG_DATA_HOME=str(tmp_path / "ignored-override"),
        ATLASSIAN_BASE_URL="https://example.invalid/",
        ATLASSIAN_EMAIL="fixture@example.invalid",
        ATLASSIAN_API_TOKEN="fixture-token",
    )
    (repo / "doc/harness/manifest.yaml").write_text(json.dumps({"type": "library", "wiki": {
        "backend": "atlassian", "enabled": True, "site": "https://example.invalid/", "space": "TEAM",
        "spaceId": "123", "rootPageId": "456",
    }}))
    identity = ["https://example.invalid", "fixture@example.invalid", "456", "123"]
    partition = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
    control = tmp_path / "home/.local/share/wiki-atlassian" / partition / "confluence/TEAM/.sync"
    control.mkdir(parents=True)
    (control / "sync.lock").touch()
    credential = ["https://example.invalid", "fixture@example.invalid", "fixture-token"]
    binding = hashlib.sha256(json.dumps(credential, separators=(",", ":")).encode()).hexdigest()
    (control / "state.json").write_text(json.dumps({"credentialBinding": binding}))
    return repo, env, control


def page_event(seq, page, kind="updated"):
    return {"seq": seq, "kind": kind, "id": str(page), "version": 2}


def publish(control, events, head=None, epoch=EPOCH):
    (control / "changes.json").write_text(json.dumps({
        "epoch": epoch, "head": len(events) if head is None else head, "events": events,
    }))


def hook(repo, env, session="session-a", runtime="codex", prompt=False, process_cwd=None):
    if prompt and runtime == "codex":
        argv = [str(SCRIPTS / "hook_user_prompt_submit.py")]
    elif prompt:
        argv = [str(SCRIPTS / "prompt_memory.py")]
    else:
        argv = [str(SCRIPTS / "wiki_change_notice.py"), runtime]
    payload = {"session_id": session, "cwd": str(repo), "tool_name": "Read"}
    result = subprocess.run([sys.executable, "-B", *argv], cwd=process_cwd or repo, env=env,
                            input=json.dumps(payload), text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    if result.stdout and (runtime == "codex" or not prompt):
        return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    return result.stdout


@pytest.mark.parametrize("runtime", ["codex", "claude"])
def test_prompt_and_tool_hooks_deliver_once_per_session(setup_wiki, runtime):
    repo, env, control = setup_wiki
    publish(control, [])
    assert "wiki-changes" not in hook(repo, env, runtime=runtime, prompt=True)
    assert not (control / "harness-cursors.json").exists()
    publish(control, [page_event(1, 101), page_event(2, 103, "comments")])
    context = hook(repo, env, runtime=runtime)
    assert "Document changed — page 101, observed version 2" in context
    assert "Comments changed — page 103" in context
    assert "document_relations" in context
    assert "fixture-token" not in context
    assert "wiki-changes" not in hook(repo, env, runtime=runtime, prompt=True)
    assert "page 101" in hook(repo, env, runtime=runtime, session="session-b", prompt=True)


def test_cursor_pages_gaps_and_recovers_new_epoch(setup_wiki):
    repo, env, control = setup_wiki
    publish(control, [page_event(i, 100 + i) for i in range(4, 16)], head=15)
    first = hook(repo, env)
    assert "intervening notices are unavailable" in first and "More notices" in first
    assert "page 104" in first and "page 111" in first and "page 112" not in first
    second = hook(repo, env)
    assert "page 112" in second and "page 115" in second and "page 111" not in second
    assert not hook(repo, env)
    publish(control, [{"seq": 1, "kind": "scope"}], epoch="2" * 32)
    assert "visible document scope changed" in hook(repo, env)
    assert not hook(repo, env)


def test_pending_publication_and_contended_lock_do_not_consume(setup_wiki):
    repo, env, control = setup_wiki
    publish(control, [page_event(1, 101)])
    transaction = control / "transaction.json"
    transaction.touch()
    assert not hook(repo, env)
    transaction.unlink()
    denied = control / "notice-access-denied"
    denied.touch()
    assert not hook(repo, env)
    denied.unlink()
    (control / "sync.lock").chmod(0o664)
    with (control / "sync.lock").open("r+b") as lock:
        fcntl.lockf(lock, fcntl.LOCK_EX)
        assert not hook(repo, env)
    assert "page 101" in hook(repo, env)


def test_wrong_connection_scope_malformed_and_unsafe_feed_are_inert(setup_wiki, tmp_path):
    repo, env, control = setup_wiki
    publish(control, [page_event(1, 101)])
    assert not hook(repo, dict(env, ATLASSIAN_API_TOKEN="another-token"))
    assert not hook(repo, dict(env, ATLASSIAN_BASE_URL="https://other.invalid"))
    assert not (control / "harness-cursors.json").exists()
    outside = tmp_path / "other"
    outside.mkdir()
    assert not hook(outside, env)
    publish(control, [dict(page_event(1, 101), title="injected directive")])
    assert not hook(repo, env)
    publish(control, [page_event(1, 101)])
    feed = control / "changes.json"
    moved = tmp_path / "copied-feed"
    feed.rename(moved)
    feed.symlink_to(moved)
    assert not hook(repo, env)
    assert not (control / "harness-cursors.json").exists()


def test_replaced_token_reuses_feed_only_after_current_authorization(setup_wiki, tmp_path):
    repo, env, control = setup_wiki
    publish(control, [page_event(1, 101)])
    replacement = dict(env, ATLASSIAN_API_TOKEN="replacement-token")
    assert not hook(repo, replacement)
    assert not (control / "harness-cursors.json").exists()
    binding = hashlib.sha256(json.dumps(["https://example.invalid", "fixture@example.invalid", "replacement-token"],
        separators=(",", ":")).encode()).hexdigest()
    (control / "state.json").write_text(json.dumps({"credentialBinding": binding}))
    assert "page 101" in hook(repo, replacement)
    assert not hook(repo, env, session="retired-session")
    assert not (tmp_path / "ignored-override").exists()


def test_generated_codex_and_claude_commands_deliver_native_context(setup_wiki, monkeypatch):
    repo, env, control = setup_wiki
    publish(control, [page_event(1, 101)])
    spec = importlib.util.spec_from_file_location("harness_install_wiki_test", ROOT / "install.py")
    install = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, install)
    spec.loader.exec_module(install)
    codex = install._codex_hooks_config(ROOT / "plugin")
    claude = json.loads((ROOT / "plugin/hooks/hooks.json").read_text())
    for runtime, config in (("codex", codex), ("claude", claude)):
        entry = config["hooks"]["PostToolUse"][-1]
        assert re.fullmatch(entry["matcher"], "Read")
        command = entry["hooks"][0]["command"]
        result = subprocess.run(
            ["sh", "-c", command], cwd=repo,
            env=dict(env, CLAUDE_PLUGIN_ROOT=str(ROOT / "plugin")),
            input=json.dumps({"cwd": str(repo), "session_id": "command-session", "tool_name": "Read"}),
            text=True, capture_output=True, timeout=10,
        )
        assert result.returncode == 0, result.stderr
        response = json.loads(result.stdout)["hookSpecificOutput"]
        assert response["hookEventName"] == "PostToolUse" and "page 101" in response["additionalContext"], runtime


def test_configured_memory_guides_prompt_and_invalid_watch_stays_pending(setup_wiki):
    repo, env, control = setup_wiki
    path = repo / "doc/harness/manifest.yaml"
    config = json.loads(path.read_text())
    config["wiki"]["memoryPageId"] = "456"
    path.write_text(json.dumps(config))
    context = hook(repo, env, prompt=True)
    assert "harness-wiki-memory" in context and "existing source reference" in context
    publish(control, [page_event(1, 101)])
    config["wiki"]["watchCommentPages"] = "101,101"
    path.write_text(json.dumps(config))
    invalid = env
    assert "remain pending" in hook(repo, invalid, prompt=True)
    assert not hook(repo, invalid)
    assert not (control / "harness-cursors.json").exists()


@pytest.mark.parametrize("runtime", ["codex", "claude"])
def test_payload_cwd_drives_both_hooks_and_old_environment_is_ignored(setup_wiki, tmp_path, runtime):
    repo, env, control = setup_wiki
    process = tmp_path / "process"; process.mkdir()
    # The process directory's local config must never replace payload selection.
    (process / "doc/harness").mkdir(parents=True)
    (process / "doc/harness/manifest.yaml").write_text('{"type":"library","wiki":{"enabled": false}}')
    stale = dict(env, WIKI_ATLASSIAN_ENABLED="false", WIKI_ATLASSIAN_SITE="https://other.invalid",
                 WIKI_ATLASSIAN_ROOT_PAGE_ID="999")
    publish(control, [page_event(1, 101)])
    assert "page 101" in hook(repo, stale, runtime=runtime, process_cwd=process, prompt=True)
    assert not hook(repo, stale, runtime=runtime, process_cwd=process)


def test_two_projects_share_home_scope_without_replay_and_different_scope_is_inert(setup_wiki, tmp_path):
    repo, env, control = setup_wiki
    other = tmp_path / "second-project"
    (other / "doc/harness").mkdir(parents=True)
    (other / "doc/harness/manifest.yaml").write_text("type: library\n")
    (other / ".git").mkdir()
    path = other / "doc/harness/manifest.yaml"
    config = json.loads((repo / "doc/harness/manifest.yaml").read_text())
    path.write_text(json.dumps(config))
    publish(control, [page_event(1, 101)])
    assert "page 101" in hook(repo, env)
    assert not hook(other, env)  # same runtime/session already consumed shared feed
    assert "page 101" in hook(other, env, session="second-session")
    config["wiki"]["rootPageId"] = "999"; path.write_text(json.dumps(config))
    assert not hook(other, env, session="different-root")
    config["wiki"]["rootPageId"] = "456"; path.write_text(json.dumps(config))
    assert not hook(other, dict(env, ATLASSIAN_EMAIL="other@example.invalid"), session="different-account")


def test_disabled_or_missing_yaml_never_reads_feed(setup_wiki):
    repo, env, control = setup_wiki
    publish(control, [page_event(1, 101)])
    path = repo / "doc/harness/manifest.yaml"
    path.write_text('{"type":"library","wiki":{"enabled": false}}')
    assert not hook(repo, env)
    path.write_text("type: library\n")
    assert not hook(repo, dict(env, WIKI_ATLASSIAN_ENABLED="true"))
    assert not (control / "harness-cursors.json").exists()
