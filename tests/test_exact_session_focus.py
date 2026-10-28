"""Exact authority is exercised through real prewrite and trusted result hooks."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

SID = 'a1b2c3d4-5e6f-7890-abcd-ef1234567890'
ROOT = Path(__file__).resolve().parents[1]


def _task(root, *, maintenance=False):
    from test_prewrite_gate_dormant import _write_task_state
    import wbs
    task = Path(_write_task_state(str(root / 'doc/harness/tasks'), 'exact', 'open'))
    run = json.loads((task / 'TASK.json').read_text())['run_id']
    plan = '# Exact fixture\n```wbs\n' + json.dumps([dict(id='A', title='Exact source', weight=1)]) + '\n```\n'
    (task / 'PLAN.md').write_text(plan)
    if maintenance:
        (task / 'MAINTENANCE').touch()
    state = wbs.prepare(str(task), plan, run)
    (task / 'WBS.json').write_text(wbs.encode_state(state))
    # Prepare the existing owner state for this gate fixture; admission itself
    # has separate public-entrypoint tests. No gate policy is mocked here.
    host = dict(capacity=3, live_agents=[dict(id='fixture', role='coordinator')],
                reserved_slots=[], verification_reserve=1, spawn_available=True,
                continuation_available=True)
    wbs.work_pack(str(task), str(root), run, bind=True,
                  ownership={'A': ['engine.py', 'plugin/scripts/prewrite_gate.py']}, host=host)
    marker = dict(session_id=SID, task_dir=str(task), task_id=task.name,
                  run_id=run, updated='2026-10-28T00:00:00Z')
    sessions = root / 'doc/harness/tasks/.active_sessions'
    sessions.mkdir(exist_ok=True)
    (sessions / (SID + '.json')).write_text(json.dumps(marker))
    return task, marker


def _write_marker(root, marker):
    (root / 'doc/harness/tasks/.active_sessions' / (SID + '.json')).write_text(json.dumps(marker))


def _gate(cwd, target, sid=SID):
    env = dict(os.environ)
    for key in ('HARNESS_SKIP_PREWRITE', 'HARNESS_DISABLE_SCOPE_LOCK', 'CODEX_THREAD_ID',
                'HARNESS_SESSION_ID', 'CODEX_SESSION_ID', 'CLAUDE_SESSION_ID'):
        env.pop(key, None)
    payload = dict(cwd=str(cwd), tool_name='Write', tool_input=dict(file_path=str(target)))
    if sid is not None:
        payload['session_id'] = sid
    p = subprocess.run([sys.executable, '-B', str(ROOT / 'plugin/scripts/prewrite_gate.py')],
                       input=json.dumps(payload), capture_output=True, text=True, cwd=cwd, env=env, timeout=10)
    assert p.returncode == 0 and not p.stderr, p.stderr
    return json.loads(p.stdout)['hookSpecificOutput'] if p.stdout.strip() else {}


def test_source_and_control_need_current_exact_run_not_legacy_or_hint(tmp_path, monkeypatch):
    from test_codex_worktree_prewrite import setup
    monkeypatch.syspath_prepend(str(ROOT / 'plugin/scripts'))
    main, _ = setup(tmp_path)
    task, marker = _task(main, maintenance=True)
    legacy = main / 'doc/harness/tasks/.active'
    legacy.write_text(str(task))
    (main / 'doc/harness/tasks/.active_sessions/.session-hint').write_text(SID)
    for rel in ('engine.py', 'plugin/scripts/prewrite_gate.py'):
        assert _gate(main, main / rel) == {}
        for sid in (None, '', 'default', 'foreign'):
            assert _gate(main, main / rel, sid).get('permissionDecision') == 'deny'
        _write_marker(main, dict(marker, run_id='0198c349-5800-7000-8000-000000000002'))
        assert _gate(main, main / rel).get('permissionDecision') == 'deny'
        _write_marker(main, dict(marker, conflicts=[]))
        assert _gate(main, main / rel).get('permissionDecision') == 'deny'
        _write_marker(main, marker)
        assert _gate(main, main / rel) == {}
    assert legacy.read_text() == str(task)


def test_registered_worktree_to_main_and_sibling_apply_target_local_gates(tmp_path, monkeypatch):
    from test_codex_worktree_prewrite import setup
    from test_worktree_workspace import _worktree
    monkeypatch.syspath_prepend(str(ROOT / 'plugin/scripts'))
    main, lead = setup(tmp_path)
    sibling = _worktree(main, 'sibling')
    for target_root in (main, sibling):
        task, marker = _task(target_root, maintenance=True)
        for rel in ('engine.py', 'plugin/scripts/prewrite_gate.py'):
            target = target_root / rel
            assert _gate(lead, target) == {}
            _write_marker(target_root, dict(marker, conflicts_overflow=True))
            assert _gate(lead, target).get('permissionDecision') == 'deny'
            _write_marker(target_root, marker)
        assert _gate(lead, task / 'TASK.json').get('permissionDecision') == 'deny'


def test_claude_hook_configuration_binds_successful_content_result(tmp_path, monkeypatch):
    from test_codex_worktree_prewrite import setup
    monkeypatch.syspath_prepend(str(ROOT / 'plugin/scripts'))
    main, _ = setup(tmp_path)
    task, marker = _task(main)
    path = main / 'doc/harness/tasks/.active_sessions' / (SID + '.json')
    path.unlink()
    tool = 'mcp__plugin_harness_harness__wbs'
    config = json.loads((ROOT / 'plugin/hooks/hooks.json').read_text())
    import re
    entries = [entry for entry in config['hooks']['PostToolUse'] if re.search(entry['matcher'], tool)]
    assert len(entries) == 1
    hook = entries[0]['hooks'][0]
    assert hook['timeout'] <= 10 and hook['command'].endswith('|| true')
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT / 'plugin'), HARNESS_RUNTIME='claude')
    for key in ('CODEX_THREAD_ID', 'HARNESS_SESSION_ID', 'CLAUDE_SESSION_ID'):
        env.pop(key, None)
    calls = [dict(jsonrpc='2.0', id=1, method='initialize', params=dict(
        protocolVersion='2025-06-18', capabilities={}, clientInfo=dict(name='claude', version='1'))),
        dict(jsonrpc='2.0', id=2, method='tools/call', params=dict(name='wbs', arguments=dict(
            action='open', wbs_id=task.name)))]
    server = subprocess.run([sys.executable, '-B', str(ROOT / 'plugin/mcp/harness_server.py')],
                            input=''.join(json.dumps(call) + '\n' for call in calls), text=True,
                            capture_output=True, cwd=main, env=env, timeout=15)
    assert server.returncode == 0, server.stderr
    response = next(json.loads(line)['result'] for line in server.stdout.splitlines()
                    if json.loads(line).get('id') == 2)
    assert not response.get('isError'), response
    assert not path.exists() and not (path.parent / 'default.json').exists()
    # Claude forwards the MCP content envelope, which lacks structuredContent.
    payload = dict(cwd=str(main), session_id=SID, tool_name=tool,
                   tool_input=dict(action='open', wbs_id=task.name),
                   tool_response=dict(content=response['content']))
    command = hook['command'].removesuffix(' || true')
    p = subprocess.run(command, input=json.dumps(payload), shell=True, text=True,
                       capture_output=True, cwd=main, env=env, timeout=10)
    assert p.returncode == 0 and not p.stderr, p.stderr
    assert path.is_file(), p.stdout
    assert json.loads(path.read_text())['run_id'] == marker['run_id']
    assert not (path.parent / 'default.json').exists()
    assert not (path.parent.parent / '.active').exists()
    payload['tool_response']['isError'] = True
    path.unlink()
    p = subprocess.run(command, input=json.dumps(payload), shell=True, text=True,
                       capture_output=True, cwd=main, env=env, timeout=10)
    assert p.returncode == 0 and not path.exists()


def test_registered_invalid_main_and_sibling_keep_target_gates_and_c05(tmp_path, monkeypatch):
    from test_codex_worktree_prewrite import setup
    from test_worktree_workspace import _worktree
    monkeypatch.syspath_prepend(str(ROOT / 'plugin/scripts'))
    import prewrite_gate
    import _lib
    main, lead = setup(tmp_path)
    sibling = _worktree(main, 'invalid-sibling')
    for target_root in (main, sibling):
        task, marker = _task(target_root, maintenance=True)
        manifest = target_root / 'doc/harness/manifest.yaml'
        original = manifest.read_bytes()
        for mutation in ('symlink', 'directory', 'missing'):
            outside = tmp_path / ('external-' + target_root.name + '.yaml')
            outside.write_bytes(original)
            manifest.unlink()
            if mutation == 'symlink':
                manifest.symlink_to(outside)
            elif mutation == 'directory':
                manifest.mkdir()
            for rel in ('engine.py', 'plugin/scripts/prewrite_gate.py'):
                result = _gate(lead, target_root / rel)
                assert result.get('permissionDecision') == 'deny', (mutation, result)
                assert 'invalid-harness-workspace' in result['permissionDecisionReason']
            target = task / 'TASK.json'
            assert _gate(lead, target).get('permissionDecision') == 'deny'
            payload = dict(cwd=str(lead), session_id=SID, tool_name='Write',
                           tool_input=dict(file_path=str(target)))
            with monkeypatch.context() as state:
                state.setattr(_lib, '_LAST_HOOK_INPUT', {})
                fallback = prewrite_gate.protected_artifact_decision(json.dumps(payload))
            assert 'C-05-protected-artifact' in fallback
            if manifest.is_symlink():
                manifest.unlink()
            elif manifest.is_dir():
                manifest.rmdir()
            manifest.write_bytes(original)
        assert _gate(lead, target_root / 'engine.py') == {}
