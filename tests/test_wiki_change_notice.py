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

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module") from error

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
        "backend": "atlassian", "enabled": True, "site": "https://example.invalid/",
        "spaces": [{"key": "TEAM", "rootPageId": "456"}],
    }}))
    identity = ["https://example.invalid", "fixture@example.invalid", "456", "TEAM"]
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


def add_space(repo, env, key, root=None, **selection):
    path = repo / "doc/harness/manifest.yaml"
    document = json.loads(path.read_text())
    space = {"key": key, **selection}
    if root is not None:
        space["rootPageId"] = root
    document["wiki"]["spaces"].append(space)
    path.write_text(json.dumps(document))
    identity = [document["wiki"]["site"].rstrip("/"), env["ATLASSIAN_EMAIL"], root or "*", key]
    partition = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
    control = Path(env["HOME"]) / ".local/share/wiki-atlassian" / partition / "confluence" / key / ".sync"
    control.mkdir(parents=True)
    (control / "sync.lock").touch()
    binding = hashlib.sha256(json.dumps([document["wiki"]["site"].rstrip("/"), env["ATLASSIAN_EMAIL"],
        env["ATLASSIAN_API_TOKEN"]], separators=(",", ":")).encode()).hexdigest()
    (control / "state.json").write_text(json.dumps({"credentialBinding": binding}))
    return control


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
    config["wiki"]["spaces"][0]["memoryPageId"] = "456"
    path.write_text(json.dumps(config))
    context = hook(repo, env, prompt=True)
    assert "harness-wiki-memory" in context and "existing source reference" in context
    publish(control, [page_event(1, 101)])
    config["wiki"]["spaces"][0]["watchCommentPages"] = "101,101"
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
    config["wiki"]["spaces"][0]["rootPageId"] = "999"; path.write_text(json.dumps(config))
    assert not hook(other, env, session="different-root")
    config["wiki"]["spaces"][0]["rootPageId"] = "456"; path.write_text(json.dumps(config))
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


def test_two_spaces_deliver_labelled_notices_with_independent_shared_cursors(setup_wiki):
    repo, env, team = setup_wiki
    dev = add_space(repo, env, "DEV")
    publish(team, [page_event(1, 101)])
    publish(dev, [page_event(1, 201, "comments")])
    context = hook(repo, env)
    assert "Space TEAM:" in context and "Space DEV:" in context
    assert "page 101" in context and "page 201" in context
    assert context.count("[harness-wiki-changes]") == 1
    assert context.count("If relevant to the current task") == 1
    assert not hook(repo, env)
    publish(dev, [page_event(1, 201, "comments"), page_event(2, 202)])
    context = hook(repo, env)
    assert "Space TEAM:" not in context and "page 202" in context
    assert "page 101" in hook(repo, env, session="independent-session")
    assert (team / "harness-cursors.json").exists() and (dev / "harness-cursors.json").exists()


@pytest.mark.parametrize("failure", ["malformed", "denied", "transaction", "wrong-credential", "busy"])
def test_unavailable_first_space_does_not_suppress_other_space_or_consume_its_feed(setup_wiki, failure):
    repo, env, team = setup_wiki
    dev = add_space(repo, env, "DEV")
    publish(team, [page_event(1, 101)])
    publish(dev, [page_event(1, 201)])
    if failure == "malformed":
        (team / "changes.json").write_text("{malformed")
    elif failure == "denied":
        (team / "notice-access-denied").touch()
    elif failure == "transaction":
        (team / "transaction.json").touch()
    elif failure == "wrong-credential":
        (team / "state.json").write_text('{"credentialBinding":"wrong"}')
    with (team / "sync.lock").open("r+b") as lock:
        if failure == "busy":
            fcntl.lockf(lock, fcntl.LOCK_EX)
        context = hook(repo, env)
    assert "page 201" in context and "Space DEV:" in context
    assert "Space TEAM:" not in context
    assert not (team / "harness-cursors.json").exists()
    assert (dev / "harness-cursors.json").exists()


def test_notice_budget_is_fair_across_maximum_spaces_and_does_not_advance_undelivered_events(setup_wiki):
    repo, env, team = setup_wiki
    controls = [team] + [add_space(repo, env, "S" + str(index)) for index in range(1, 32)]
    for index, control in enumerate(controls):
        publish(control, [page_event(seq, index * 100 + seq) for seq in range(1, 65)])
    first = hook(repo, env)
    assert first.count("Document changed —") == 32
    for key in ["TEAM", *("S" + str(i) for i in range(1, 32))]:
        assert f"Space {key}:" in first
    assert first.count("More notices will appear") == 32
    assert first.count("If relevant to the current task") == 1
    for control in controls:
        cursors = json.loads((control / "harness-cursors.json").read_text())
        assert list(cursors.values()) == [[EPOCH, 1]]
    second = hook(repo, env)
    assert second.count("Document changed —") == 32
    for control in controls:
        cursors = json.loads((control / "harness-cursors.json").read_text())
        assert list(cursors.values()) == [[EPOCH, 2]]


def test_memory_destinations_name_each_space_and_do_not_choose_the_first(setup_wiki):
    repo, env, control = setup_wiki
    path = repo / "doc/harness/manifest.yaml"
    document = json.loads(path.read_text())
    document["wiki"]["spaces"][0]["memoryPageId"] = "456"
    path.write_text(json.dumps(document))
    add_space(repo, env, "DEV", memoryPageId="789")
    context = hook(repo, env, prompt=True)
    assert "TEAM: page 456" in context and "DEV: page 789" in context
    assert "never choose the first space" in context
    assert "fixture-token" not in context


def test_local_notice_path_requires_no_network_resolution(setup_wiki, monkeypatch):
    import socket
    repo, env, control = setup_wiki
    publish(control, [page_event(1, 101)])
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("wiki_notice_offline_test", SCRIPTS / "wiki_change_notice.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    def unexpected_network(*args, **kwargs):
        raise AssertionError("notice hooks must never resolve remote space IDs")
    monkeypatch.setattr(socket, "socket", unexpected_network)
    result = module.render_notice({"session_id": "offline", "cwd": str(repo)}, "codex", env)
    assert "Space TEAM:" in result and "page 101" in result


def test_many_memory_destinations_use_bounded_explicit_selection_summary(setup_wiki):
    repo, env, control = setup_wiki
    path = repo / "doc/harness/manifest.yaml"
    document = json.loads(path.read_text())
    document["wiki"]["spaces"][0]["memoryPageId"] = "456"
    path.write_text(json.dumps(document))
    for index in range(1, 32):
        add_space(repo, env, "S" + str(index), memoryPageId=str(456 + index))
    context = hook(repo, env, runtime="claude", prompt=True)
    assert "32 destinations; inspect connection_status" in context
    assert "never choose the first space" in context
    assert "TEAM: page" not in context and len(context) <= 400
