"""Migration recovery authority stays owned by the public migration tool."""
import json
from pathlib import Path

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module")

from conftest import SCRIPTS_DIR, invoke_hook, make_tmp_harness_root, parse_decision


TREE = Path('doc/harness/archive/task-control-migration')


@pytest.mark.parametrize('name', ['manifest.json', '.lock', 'nested/original.json'])
@pytest.mark.parametrize('absolute', [False, True])
@pytest.mark.parametrize('tool', ['Write', 'Edit', 'apply_patch'])
def test_task_control_migration_tree_denies_direct_and_fallback_writes(tmp_path, monkeypatch, name, absolute, tool):
    monkeypatch.syspath_prepend(SCRIPTS_DIR)
    import _lib
    import prewrite_gate
    monkeypatch.setattr(_lib, '_LAST_HOOK_INPUT', _lib.last_hook_input())
    monkeypatch.delenv('HARNESS_SKIP_PREWRITE', raising=False)
    root = Path(make_tmp_harness_root(tmp_path / 'main'))
    path = root / TREE / name if absolute else TREE / name
    data = {'patch': f'*** Begin Patch\n*** Add File: {path}\n+forged\n*** End Patch'} if tool == 'apply_patch' else {'file_path': str(path), 'content': 'forged'}
    assert prewrite_gate._is_protected_artifact(str(path), repo_root=str(root))
    result = invoke_hook(str(Path(SCRIPTS_DIR) / 'prewrite_gate.py'), tool, data, cwd=str(root))
    assert result.returncode == 0 and not result.stderr
    decision, reason = parse_decision(result.stdout)
    assert decision == 'deny'
    assert 'C-05-protected-artifact' in reason and 'legacy-data-migration' in reason
    fallback = prewrite_gate.protected_artifact_decision(json.dumps(dict(cwd=str(root), tool_name=tool, tool_input=data)))
    assert parse_decision(fallback)[0] == 'deny'
    assert not (root / TREE).exists()


def test_task_control_migration_is_protected_across_linked_checkouts(tmp_path, monkeypatch):
    from test_codex_worktree_prewrite import check, setup
    monkeypatch.delenv('HARNESS_SKIP_PREWRITE', raising=False)
    main, worktree = setup(tmp_path)
    denied = check(main, worktree / TREE / 'manifest.json')
    assert denied.get('permissionDecision') == 'deny'
    assert 'C-05-protected-artifact' in denied['permissionDecisionReason']
    assert 'legacy-data-migration' in denied['permissionDecisionReason']
    assert not (worktree / TREE).exists()
