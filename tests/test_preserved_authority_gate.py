"""Receipt removal keeps native authentication and historical bytes immutable."""
import json
from datetime import datetime
from pathlib import Path

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module")

from conftest import SCRIPTS_DIR, invoke_hook, make_tmp_harness_root, parse_decision


THREAD = '019f82a6-ce64-75a3-b01d-92f7b0b4fe6f'


def _assert_denied(root, target, tool, owner, monkeypatch):
    monkeypatch.syspath_prepend(SCRIPTS_DIR)
    import _lib
    import prewrite_gate
    monkeypatch.setattr(_lib, '_LAST_HOOK_INPUT', _lib.last_hook_input())
    data = {'patch': f'*** Begin Patch\n*** Update File: {target}\n-old\n+forged\n*** End Patch'} if tool == 'apply_patch' else {'file_path': str(target), 'content': 'forged'}
    result = invoke_hook(str(Path(SCRIPTS_DIR) / 'prewrite_gate.py'), tool, data, cwd=str(root))
    assert result.returncode == 0 and not result.stderr
    decision, reason = parse_decision(result.stdout)
    assert decision == 'deny' and owner in reason
    fallback = prewrite_gate.protected_artifact_decision(json.dumps(dict(cwd=str(root), tool_name=tool, tool_input=data)))
    assert parse_decision(fallback)[0] == 'deny'
    assert owner in fallback


@pytest.mark.parametrize('tool', ['Write', 'Edit', 'apply_patch'])
@pytest.mark.parametrize('alias', [False, True])
@pytest.mark.parametrize('cwd_kind', ['main', 'unrelated'])
@pytest.mark.parametrize('hook_cwd_kind', ['main', 'unrelated'])
def test_native_session_authentication_metadata_cannot_be_forged(tmp_path, monkeypatch, tool, alias, cwd_kind, hook_cwd_kind):
    monkeypatch.syspath_prepend(SCRIPTS_DIR)
    import _lib
    from codex_hook_registration import authenticate_native_session
    monkeypatch.delenv('HARNESS_SKIP_PREWRITE', raising=False)
    root = Path(make_tmp_harness_root(tmp_path / 'main'))
    other = tmp_path / 'unrelated'
    other.mkdir()
    home = tmp_path / 'codex-home'
    monkeypatch.setenv('CODEX_HOME', str(home))
    monkeypatch.setenv('CODEX_THREAD_ID', THREAD)
    created = datetime.fromtimestamp(_lib.uuid7_timestamp_ms(THREAD) / 1000).astimezone()
    native = home / 'sessions' / f'{created:%Y/%m/%d}' / f'rollout-test-{THREAD}.jsonl'
    native.parent.mkdir(parents=True)
    cwd = root if cwd_kind == 'main' else other
    original = (json.dumps(dict(type='session_meta', payload=dict(id=THREAD, cwd=str(cwd)))) + '\n').encode()
    native.write_bytes(original)
    expected = str(root) if cwd_kind == 'main' else ''
    assert authenticate_native_session(THREAD, str(root)) == expected
    target = root / 'metadata-alias.jsonl' if alias else native
    if alias:
        target.symlink_to(native)
    hook_cwd = root if hook_cwd_kind == 'main' else other
    _assert_denied(hook_cwd, target, tool, 'native-session-runtime', monkeypatch)
    assert native.read_bytes() == original
    assert authenticate_native_session(THREAD, str(root)) == expected


@pytest.mark.parametrize('tool', ['Write', 'Edit', 'apply_patch'])
@pytest.mark.parametrize('name', ['RECEIPTS.jsonl', 'REVIEWS.jsonl'])
def test_historical_outputs_are_preserved_without_a_writer(tmp_path, monkeypatch, tool, name):
    monkeypatch.delenv('HARNESS_SKIP_PREWRITE', raising=False)
    root = Path(make_tmp_harness_root(tmp_path / 'main'))
    target = root / 'doc/harness/tasks/TASK__history' / name
    target.parent.mkdir(parents=True)
    original = b'\xffunparsed historical bytes\n'
    target.write_bytes(original)
    _assert_denied(root, target, tool, 'historical-task-artifacts', monkeypatch)
    assert target.read_bytes() == original
