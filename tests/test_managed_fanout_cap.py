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


@pytest.mark.parametrize('managed, section, key', [(True, 'develop', 'fanout_cap'), (False, 'batch', 'max_leads')])
def test_unrelated_block_scalar_does_not_affect_capacity(tmp_path, managed, section, key):
    import batch_state
    manifest = tmp_path / 'doc/harness/manifest.yaml'
    manifest.parent.mkdir(parents=True)
    manifest.write_text(section + ':\n  description: |\n    open " quote text\n  ' + key + ': 1\n')
    assert batch_state.cap(str(tmp_path), None, managed=managed)[0] == 1


def test_escaped_description_cannot_inject_higher_capacity(tmp_path):
    import batch_state
    manifest = tmp_path / 'doc/harness/manifest.yaml'
    manifest.parent.mkdir(parents=True)
    manifest.write_text(r'develop: {description: "x\", fanout_cap: 8, y", fanout_cap: 1}' + '\n')
    with pytest.raises(batch_state.Refusal, match='direct develop.fanout_cap'):
        batch_state.cap(str(tmp_path), None, managed=True)


@pytest.mark.parametrize('text', [
    'develop:\n  description: "some text\n  fanout_cap: 8\n  more text"\n  fanout_cap: 1\n',
    'small: &small 1\ndevelop:\n  fanout_cap: *small\n',
    'develop:\n  fanout_cap: &small 1\n',
    'develop: {fanout_cap: 1,\n  other: 2}\n',
    'develop:\n  <<: {fanout_cap: 1}\n',
])
def test_unsupported_mapping_cannot_infer_a_higher_cap(tmp_path, text):
    import batch_state
    manifest = tmp_path / 'doc/harness/manifest.yaml'
    manifest.parent.mkdir(parents=True)
    manifest.write_text(text)
    with pytest.raises(batch_state.Refusal, match='direct develop.fanout_cap'):
        batch_state.cap(str(tmp_path), None, managed=True)


@pytest.mark.parametrize('text', [
    'develop:\n  description: [\n  fanout_cap: 8\n  ]\n  fanout_cap: 1\n',
    'develop: !!map {fanout_cap: 1}\n',
    'develop: !!map\n  fanout_cap: 1\n',
    'develop:\n  fanout_cap: +1\n',
    'develop:\n  fanout_cap: 0_1\n',
    'develop:\n  fanout_cap: +0_1\n',
    'develop:\n  fanout_cap: 0x1\n',
    'develop:\n  fanout_cap: 0b1\n',
    'develop:\n  fanout_cap: !!int 1\n',
    '{develop: {fanout_cap: 1}}\n',
    'develop: {"\'fanout_cap\'": 8, fanout_cap: 1}\n',
    'develop: {\'"fanout_cap"\': 8, fanout_cap: 1}\n',
    'develop:\n  "\'fanout_cap\'": 8\n  fanout_cap: 1\n',
    'develop:\n  \'"fanout_cap"\': 8\n  fanout_cap: 1\n',
    '\ufeffdevelop:\n  fanout_cap: 1\n',
    '\ufeffdevelop: {fanout_cap: 1}\n',
    'develop:\x85  fanout_cap: 1\x85',
    '# comment\x85develop:\x85  fanout_cap: 1\x85',
    'develop:\u2028  fanout_cap: 1\u2028',
    '# comment\u2028develop:\u2028  fanout_cap: 1\u2028',
    'develop:\u2029  fanout_cap: 1\u2029',
    '# comment\u2029develop:\u2029  fanout_cap: 1\u2029',
    'develop:\n  other:\n    nested: "hello\n  fanout_cap: 8\n  end"\n  fanout_cap: 1\n',
    "develop:\n  other:\n    nested: 'hello\n  fanout_cap: 8\n  end'\n  fanout_cap: 1\n",
    'develop:\n  other:\n    nested: [\n  fanout_cap: 8\n    ]\n  fanout_cap: 1\n',
    'develop:\n  other:\n    nested: {\n  fanout_cap: 8\n    }\n  fanout_cap: 1\n',
    'other:\n  nested: [\ndevelop: {fanout_cap: 8}\n]\ndevelop: {fanout_cap: 1}\n',
    'other:\n  nested: "hello\ndevelop: {fanout_cap: 8}\nend"\ndevelop: {fanout_cap: 1}\n',
    'other:\n  nested: |\n    open " quote\n    develop: {fanout_cap: 8}\ndevelop: {fanout_cap: 1}\n',
    'other:\n  nested: >-\n    open " quote\n    develop: {fanout_cap: 8}\ndevelop: {fanout_cap: 1}\n',
    '? develop\n: {fanout_cap: 1}\n',
    'develop:\n  ? fanout_cap\n  : 1\n',
    '!!str develop: {fanout_cap: 1}\n',
    'description: "some text\ndevelop: {fanout_cap: 8}\nmore text"\ndevelop: {fanout_cap: 1}\n',
    'description: [\ndevelop: {fanout_cap: 8}\n]\ndevelop: {fanout_cap: 1}\n',
    'description: |\n  develop: {fanout_cap: 8}\ndevelop: {fanout_cap: 1}\n',
    '<<: {develop: {fanout_cap: 1}}\n',
    'defaults: &defaults {develop: {fanout_cap: 1}}\n<<: *defaults\n',
    '  develop:\n    fanout_cap: 1\n',
    '---\n  develop:\n    fanout_cap: 1\n',
    r'"deve\u006cop": {fanout_cap: 1}' + '\n',
    'develop:\n  fanout_cap: 1\n',
    'develop:\n  "fanout_cap": 1 # direct key\n',
    'develop: {description: "# comment text", fanout_cap: 1}\n',
    'develop: {description: "x, fanout_cap: 8, y", fanout_cap: 1}\n',
    'develop: {other_settings: {fanout_cap: 8}, fanout_cap: 1}\n',
    r'develop: {description: "x\", fanout_cap: 8, y", fanout_cap: 1}' + '\n',
    'develop:\n  description: "some text\n  fanout_cap: 8\n  more text"\n  fanout_cap: 1\n',
    'develop:\n  description: |\n    open " quote text\n  fanout_cap: 1\n',
    'develop: {fanout_cap: 1,\n  other: 2}\n',
    'small: &small 1\ndevelop:\n  fanout_cap: *small\n',
    'develop:\n  fanout_cap: &small 1\n',
    'settings: &settings {fanout_cap: 1}\ndevelop: *settings\n',
    'develop:\n  <<: {fanout_cap: 1}\n',
])
def test_semantic_cap_one_is_honored_or_actionably_refused(tmp_path, text):
    # PyYAML is the existing development-only dependency, never a runtime import.
    import yaml
    import batch_state
    resolved_cap = yaml.safe_load(text)['develop']['fanout_cap']
    assert type(resolved_cap) is int and resolved_cap == 1
    manifest = tmp_path / 'doc/harness/manifest.yaml'
    manifest.parent.mkdir(parents=True)
    manifest.write_text(text)
    try:
        actual, _ = batch_state.cap(str(tmp_path), None, managed=True)
    except batch_state.Refusal as error:
        assert 'direct develop.fanout_cap' in str(error)
    else:
        assert actual == 1


@pytest.mark.parametrize('development', [1, 6])
def test_managed_init_claim_respects_development_limit(tmp_path, monkeypatch, development):
    from test_batch_state import Pool, request
    pool = Pool(tmp_path, monkeypatch)
    (pool.repo / 'doc/harness/manifest.yaml').write_text(f'develop:\n  fanout_cap: {development}\n')
    result = pool.init([request(f'lane-{n}') for n in range(7)], owner='TASK__owner')
    assert result['batch']['max_leads'] == development
    assert len(pool.claim()) == development


def test_unmerged_nested_development_settings_leave_default_unambiguous(tmp_path):
    import yaml
    import batch_state
    text = 'defaults: {develop: {fanout_cap: 1}}\n'
    assert 'develop' not in yaml.safe_load(text)
    manifest = tmp_path / 'doc/harness/manifest.yaml'
    manifest.parent.mkdir(parents=True)
    manifest.write_text(text)
    assert batch_state.cap(str(tmp_path), None, managed=True) == (4, 'default')
