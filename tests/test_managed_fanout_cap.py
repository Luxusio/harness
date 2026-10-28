"""Managed capacity follows development settings; standalone pools retain batch settings."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugin/scripts"))

import pytest


@pytest.mark.parametrize('develop, expected', [(None, 4), ('1', 1), ('6', 6), ('99', 8),
    ('0', 4), ('-1', 4), ('true', 4), ("'6'", 4), ('2.5', 4), ('null', 4), ('[6]', 4), ('8#plain-string', 4)])
def test_managed_cap_uses_effective_development_value(tmp_path, develop, expected):
    import batch_state
    manifest = tmp_path / 'doc/harness/manifest.yaml'
    manifest.parent.mkdir(parents=True)
    manifest.write_text('batch:\n  max_leads: 2\n' +
                        ('' if develop is None else 'develop:\n  fanout_cap: ' + develop + '\n'))
    assert batch_state.cap(str(tmp_path), None, managed=True)[0] == expected
    assert batch_state.cap(str(tmp_path), '7', managed=True)[0] == min(7, expected)
    assert batch_state.cap(str(tmp_path), '1', managed=True)[0] == 1
    assert batch_state.cap(str(tmp_path), None)[0] == 2
    assert batch_state.cap(str(tmp_path), '7') == (7, 'explicit')


@pytest.mark.parametrize('development, batch_cap', [(1, 6), (6, 1)])
def test_managed_init_and_prepare_replay_pin_cap(tmp_path, monkeypatch, development, batch_cap):
    import batch_worktrees
    import work_pack
    from test_batch_state import Pool, request
    pool = Pool(tmp_path, monkeypatch)
    manifest = pool.repo / 'doc/harness/manifest.yaml'
    manifest.write_text(f'develop:\n  fanout_cap: {development}\nbatch:\n  max_leads: {batch_cap}\n')
    requests = [request('a'), request('b')]
    spec = work_pack.make_spec(str(pool.repo), 'TASK__owner', requests)
    pool.batch = spec['batch_id']
    # Claim nothing: this exercises real pool creation/replay without allocating worktrees.
    first = batch_worktrees.execute(str(pool.repo), spec, available_slots=0)
    assert first['batch']['max_leads'] == development
    assert first['batch']['cap_source'] == 'develop.fanout_cap'
    manifest.write_text('develop:\n  fanout_cap: 8\nbatch:\n  max_leads: 8\n')
    replay = batch_worktrees.execute(str(pool.repo), spec, available_slots=0)
    assert replay['batch']['max_leads'] == development
    assert replay['batch']['cap_source'] == 'develop.fanout_cap'
    assert replay['handoffs'] == first['handoffs'] == []
    assert [item['status'] for item in replay['batch']['requests']] == ['queued', 'queued']


@pytest.mark.parametrize('text, expected', [
    ('develop: {fanout_cap: 1} # actual comment\n', 1),
    ('unrelated: |\n  open \" quote text\ndevelop: {fanout_cap: 1}\n', 1),
    ('develop: {fanout_cap: 1}\n', 1),
    ('develop: {description: "# ordinary description", fanout_cap: 1}\n', 1),
    ('develop: {other_settings: {foo: 2}, fanout_cap: 1}\n', 1),
    ('develop:\n  "fanout_cap": 1\n', 1),
    ("develop:\n  'fanout_cap': 1\n", 1),
    ('develop: {other: 2, fanout_cap: 6}\n', 6),
    ('develop: {description: \"x, fanout_cap: 8, y\", fanout_cap: 1}\n', 1),
    ('develop:\n  other_settings:\n    fanout_cap: 8\n  fanout_cap: 1\n', 1),
    ('develop:\n  other_settings:\n    fanout_cap: 1\n', 4),
])
def test_managed_cap_is_direct_mapping_field(tmp_path, text, expected):
    import batch_state
    manifest = tmp_path / 'doc/harness/manifest.yaml'
    manifest.parent.mkdir(parents=True)
    manifest.write_text(text)
    assert batch_state.cap(str(tmp_path), None, managed=True)[0] == expected


def test_managed_cli_explicit_limit_is_an_additional_bound(tmp_path, monkeypatch):
    from test_batch_state import Pool, request
    pool = Pool(tmp_path, monkeypatch)
    (pool.repo / 'doc/harness/manifest.yaml').write_text('develop:\n  fanout_cap: 6\n')
    result = pool.init([request('a'), request('b')], cap='1', owner='TASK__owner')
    assert result['batch']['max_leads'] == 1
    assert result['batch']['cap_source'] == 'explicit'
    assert [item['slug'] for item in pool.claim()] == ['a']


@pytest.mark.parametrize('text', ['develop: *settings\n', 'develop: {other: 2,\n  fanout_cap: 1}\n'])
def test_unsupported_capacity_mapping_refuses_instead_of_raising_limit(tmp_path, text):
    import batch_state
    manifest = tmp_path / 'doc/harness/manifest.yaml'
    manifest.parent.mkdir(parents=True)
    manifest.write_text(text)
    with pytest.raises(batch_state.Refusal, match='direct develop.fanout_cap'):
        batch_state.cap(str(tmp_path), None, managed=True)
