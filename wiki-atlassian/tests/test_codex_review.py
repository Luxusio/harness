"""A review needs a visibly isolated host, authorized reads and a saved-revision body."""
import json
import os
import pytest

from wiki_atlassian import codex_review as review


TARGET = {'id': '42', 'version': 3, 'space': 'SD'}
THREAD = '00000000-0000-4000-8000-000000000042'


@pytest.mark.skipif(os.name == 'nt', reason='CCC POSIX mise launcher')
def test_mise_host_launches_declared_node_and_codex_without_reentering_shim(tmp_path, monkeypatch):
    import subprocess
    import native_cli
    import wiki_platform
    shim = tmp_path / 'codex'
    shim.write_text('#!/bin/sh\nexec ~/.local/bin/mise exec node@23 -- codex "$@"\n')
    tree = tmp_path / 'installed-node'
    package = tree / 'lib/node_modules/@openai/codex'
    (package / 'bin').mkdir(parents=True)
    (tree / 'bin').mkdir()
    node, entry = tree / 'bin/node', package / 'bin/codex.js'
    node.touch(mode=0o700)
    entry.touch()
    (tree / 'bin/codex').symlink_to(entry)
    manifest = package / 'package.json'
    manifest.write_text(json.dumps({'name': '@openai/codex', 'bin': {'codex': 'bin/codex.js'}}))
    env = {'HOME': str(tmp_path), 'PATH': str(tmp_path)}
    monkeypatch.setattr(native_cli, 'host_command', lambda *args, **kwargs: [str(shim)])
    def locate(argv, *, cwd, env, timeout, limit):
        assert argv == [str(tmp_path / '.local/bin/mise'), 'where', 'node@23']
        assert cwd == tmp_path and env['PATH'] == str(tmp_path)
        return subprocess.CompletedProcess(argv, 0, str(tree).encode() + b'\n')
    monkeypatch.setattr(wiki_platform, 'run_bounded', locate)
    assert review._host_argv(tmp_path, env) == [str(node), str(entry)]
    manifest.write_text(json.dumps({'name': 'another-package', 'bin': {'codex': 'bin/codex.js'}}))
    with pytest.raises(review.ReviewUnavailable):
        review._host_argv(tmp_path, env)
    shim.write_text('#!/bin/sh\nexec my-custom-codex "$@"\n')
    assert review._host_argv(tmp_path, env) == [str(shim)]


class Catalog:
    def __init__(self, *entries):
        self.entries = entries

    def call(self, method, params):
        assert method == 'mcpServerStatus/list' and params['threadId'] == THREAD
        return {'data': self.entries}


def test_catalog_requires_only_the_connected_read_server():
    permitted = dict(name=review._SERVER, runtimeStatus='connected',
                     tools={tool: {} for tool in review.REVIEW_TOOLS})
    disabled = dict(name='old-writer', runtimeStatus='disabled', tools={})
    review._review_catalog(Catalog(disabled, permitted), THREAD)
    for entry in (dict(disabled, runtimeStatus='starting'), dict(disabled, tools={'write': {}}),
                  dict(disabled, serverInfo={'name': 'unexpected'})):
        with pytest.raises(review.ReviewUnavailable):
            review._review_catalog(Catalog(permitted, entry), THREAD)
    with pytest.raises(review.ReviewUnavailable):
        review._review_catalog(Catalog(dict(permitted, tools={**permitted['tools'], 'write': {}})), THREAD)


def _approval():
    return {'threadId': THREAD, 'turnId': 'turn', 'mode': 'form', 'serverName': review._SERVER,
            'requestedSchema': {'type': 'object', 'properties': {}},
            '_meta': {'codex_approval_kind': 'mcp_tool_call', 'tool_name': 'get_document',
                      'tool_params': {'space': 'SD', 'document': '42'}}}


def test_approval_is_limited_to_one_read_in_the_current_project_thread_and_turn():
    grant = (THREAD, 'turn', 'SD')
    original = _approval()
    assert review._approved_read(original, grant)
    assert not review._approved_read(original, None)
    for changes in ({'serverName': 'writer'}, {'turnId': 'other'}, {'threadId': 'other'},
                    {'mode': 'url'}, {'requestedSchema': {'properties': {'password': {}}}}):
        assert not review._approved_read(dict(original, **changes), grant)
    for changes in ({'tool_name': 'update_document'}, {'codex_approval_kind': 'connect'},
                    {'tool_params': {'space': 'SD', 'connection': 'personal'}},
                    {'tool_params': {}}, {'tool_params': {'space': 'OTHER'}},
                    {'tool_params': {'space': 'SD', 'reads': [{'space': 'OTHER'}]}}):
        assert not review._approved_read(dict(original, _meta={**original['_meta'], **changes}), grant)


def _item(row):
    return dict(type='mcpToolCall', server=review._SERVER, tool='get_document', status='completed',
                result={'content': [{'type': 'text', 'text': json.dumps(row)}]})


def test_review_requires_native_body_at_expected_revision_and_space():
    row = dict(id='42', version=3, space='SD', content='### Evidence', format='markdown')
    assert review._read_proof(_item({'connection': 'project', 'documents': [row]}), TARGET)
    for change in ({'version': 4}, {'id': '43'}, {'space': 'OTHER'}, {'format': 'outline'},
                   {'content': '---\npage_id: 42\n---\n'}, {'content': ''}):
        assert not review._read_proof(_item({**row, **change}), TARGET)
    assert not review._read_proof(_item(dict(row, connection='personal')), TARGET)
    with pytest.raises(review.ReviewUnavailable):
        review._read_proof(dict(_item(row), server='inherited'), TARGET)


def test_bounded_summary_identifies_target_location_and_resolution():
    decision = dict(status='FAIL', findings=[dict(category='evidence', section='2.1',
                    correction='Separate the target from the measured outcome.')])
    result = review._result(json.dumps(decision), TARGET, True, THREAD)
    assert result['status'] == 'FAIL' and '2.1' in result['summary'] and 'measured' in result['summary']
    for value, proof in ((decision, False), (dict(status='PASS', findings=decision['findings']), True),
                         (dict(status='FAIL', findings=[dict(decision['findings'][0], correction='secret=ABCDEFG123456')]), True)):
        with pytest.raises(review.ReviewUnavailable):
            review._result(json.dumps(value), TARGET, proof, THREAD)
