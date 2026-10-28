"""Absolute Codex patch targets use their registered worktree's existing gates."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


GATE = Path(__file__).resolve().parents[1] / 'plugin/scripts/prewrite_gate.py'


def setup(tmp_path):
    from test_worktree_workspace import _repo, _worktree, _git

    main = _repo(tmp_path / 'main')
    manifest = main / 'doc/harness/manifest.yaml'
    manifest.write_text(manifest.read_text() + 'strict_compliance_requires_delegation: true\n')
    _git(main, 'add', 'doc/harness/manifest.yaml')
    _git(main, 'commit', '-qm', 'strict source gate')
    return main, _worktree(main, 'lead')


def active(root, plan=False):
    from test_prewrite_gate_dormant import _write_task_state

    tasks = root / 'doc/harness/tasks'
    task = Path(_write_task_state(str(tasks), 'lead', 'open'))
    (tasks / '.active').write_text(str(task) + '\n')
    if plan:
        (task / 'PLAN.md').write_text('# Approved scope\n')
    return task


def check(main, target, **extra):
    payload = dict(cwd=str(main), session_id='codex-prewrite-fixture', tool_name='apply_patch',
                   tool_input={'patch': f'*** Begin Patch\n*** Add File: {target}\n+x = 1\n*** End Patch'}, **extra)
    env = {k: v for k, v in os.environ.items()
           if k not in {'HARNESS_SKIP_PREWRITE', 'HARNESS_DISABLE_SCOPE_LOCK'}}
    env['HARNESS_RUNTIME'] = 'codex'
    result = subprocess.run([sys.executable, str(GATE)], input=json.dumps(payload),
                            text=True, capture_output=True, cwd=main, env=env, timeout=10)
    assert result.returncode == 0, result.stderr
    assert not result.stderr, result.stderr
    return json.loads(result.stdout)['hookSpecificOutput'] if result.stdout.strip() else {}


def test_worktree_source_requires_its_own_plan_from_main_cwd(tmp_path):
    main, worktree = setup(tmp_path)
    active(main, plan=True)
    task = active(worktree)
    result = check(main, worktree / 'engine.py')
    assert result.get('permissionDecision') == 'deny'
    assert 'C-02-plan-first' in result['permissionDecisionReason']
    (task / 'PLAN.md').write_text('# Approved worktree scope\n')
    assert check(main, worktree / 'engine.py').get('permissionDecision') == 'deny'


def test_worktree_plan_alone_does_not_assign_source_when_main_has_no_task(tmp_path):
    main, worktree = setup(tmp_path)
    assert 'no-active-task' in check(main, worktree / 'engine.py')['permissionDecisionReason']
    active(worktree, plan=True)
    assert check(main, worktree / 'engine.py').get('permissionDecision') == 'deny'


def test_worktree_and_sibling_protected_artifacts_stay_denied(tmp_path):
    from test_worktree_workspace import _worktree

    main, worktree = setup(tmp_path)
    sibling = _worktree(main, 'sibling')
    active(worktree, plan=True)
    active(sibling, plan=True)
    for root in (worktree, sibling):
        for name in ('TASK.json', 'PLAN.md', 'WBS.json'):
            result = check(main, root / 'doc/harness/tasks/TASK__lead' / name)
            assert result.get('permissionDecision') == 'deny'
            assert 'C-05-protected-artifact' in result['permissionDecisionReason']


def test_worktree_scope_lock_is_used_instead_of_main_scope(tmp_path):
    main, worktree = setup(tmp_path)
    active(main, plan=True)
    task = active(worktree, plan=True)
    (task / 'PROGRESS.md').write_text('forbidden_paths:\n  - engine.py\nallowed_paths:\n  - other.py\n')
    result = check(main, worktree / 'engine.py')
    assert result.get('permissionDecision') == 'deny'
    assert 'scope-lock-forbidden' in result['permissionDecisionReason']


def test_symlink_outside_worktree_does_not_inherit_main_plan(tmp_path):
    main, worktree = setup(tmp_path)
    active(main, plan=True)
    active(worktree, plan=True)
    (main / 'outside.py').write_text('x = 0\n')
    (worktree / 'escape.py').symlink_to(main / 'outside.py')
    (worktree / 'escape_dir').symlink_to(main, target_is_directory=True)
    for target in (worktree / 'escape.py', worktree / 'escape_dir/outside.py'):
        result = check(main, target)
        assert 'symlink-outside-control' in result['permissionDecisionReason']


def test_foreign_and_unregistered_targets_keep_existing_policy(tmp_path):
    from test_worktree_workspace import _repo

    main, worktree = setup(tmp_path)
    foreign = _repo(tmp_path / 'foreign')
    active(main, plan=True)
    # No new general authorizer: unrelated source writes keep existing C-12
    # behavior, while cross-checkout protected artifacts remain denied.
    assert check(main, foreign / 'engine.py', workspace=str(worktree)) == {}
    protected = foreign / 'doc/harness/tasks/TASK__foreign/PLAN.md'
    assert 'C-05-protected-artifact' in check(main, protected)['permissionDecisionReason']
    fake = main / '.claude/worktrees/unregistered'
    (fake / 'doc/harness').mkdir(parents=True)
    (fake / 'doc/harness/manifest.yaml').write_text('version: 5\n')
    (fake / '.git').write_text('gitdir: /nonexistent/worktree\n')
    active(fake)
    assert check(main, fake / 'engine.py').get('permissionDecision') == 'deny'


def test_symlink_outside_main_is_still_denied(tmp_path):
    from test_worktree_workspace import _repo

    main, _ = setup(tmp_path)
    foreign = _repo(tmp_path / 'foreign')
    (main / 'escape').symlink_to(foreign, target_is_directory=True)
    result = check(main, main / 'escape/engine.py')
    assert 'symlink-outside-control' in result['permissionDecisionReason']


def test_protected_only_fallback_resolves_worktree_without_enabling_source_gate(tmp_path):
    from test_prewrite_gate_payload_size_and_timeout import _load, _escapes_unset
    from unittest import mock
    import _lib as lib

    main, worktree = setup(tmp_path)
    gate = _load('prewrite_gate')
    payload = dict(cwd=str(main), tool_name='Write', tool_input={'file_path': str(worktree / 'engine.py')})
    # Hooks normally run in their own process. Restore their cached input here
    # so later MCP tests resolve their own cwd instead of this scratch checkout.
    previous_input = lib.last_hook_input()
    with _escapes_unset(), mock.patch.object(lib, '_LAST_HOOK_INPUT', {}):
        assert gate.protected_artifact_decision(json.dumps(payload)) == ''
        payload['tool_input']['file_path'] = str(worktree / 'doc/harness/tasks/TASK__lead/PLAN.md')
        result = json.loads(gate.protected_artifact_decision(json.dumps(payload)))
    assert lib.last_hook_input() is previous_input
    assert result['hookSpecificOutput']['permissionDecision'] == 'deny'
    assert 'C-05-protected-artifact' in result['hookSpecificOutput']['permissionDecisionReason']


def test_direct_registered_worktree_outside_main_uses_own_plan(tmp_path):
    from test_worktree_workspace import _git

    main, _ = setup(tmp_path)
    external = tmp_path / 'external-worktree'
    _git(main, 'worktree', 'add', '-q', '-b', 'external-lead', str(external))
    task = active(external)
    assert 'C-02-plan-first' in check(main, external / 'engine.py')['permissionDecisionReason']
    (task / 'PLAN.md').write_text('# Approved external worktree scope\n')
    assert check(main, external / 'engine.py').get('permissionDecision') == 'deny'
