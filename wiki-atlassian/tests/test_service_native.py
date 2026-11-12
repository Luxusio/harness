"""Exercise the service, real TLS transport, rich edits and shared replica together."""
from test_remote import native  # Reuse the native runner; never duplicate its driver.

from wiki_atlassian.replica import Replica
from wiki_atlassian.service import WikiService


def service(native):
    state, remote, settings = native
    replica = Replica(settings, remote)
    replica.synchronize()
    return state, WikiService(settings, remote, replica), replica


def test_native_edit_publishes_versioned_unicode_read_and_history(native):
    state, api, replica = service(native)
    result = api.execute('update_document', dict(document='11', version=1,
        content='<h2>Title</h2><p>한글 😀</p>', includeBody=True, metadata=True))
    assert result['status'] == 'updated' and result['version'] == 2 and result['verified']
    current = api.execute('get_document', dict(document='11', format='storage'))
    assert current['version'] == 2 and '한글 😀' in current['content']
    history = api.execute('list_document_versions', dict(document='11'))
    assert history['status'] == 'listed' and history['versions'][0]['number'] == 2
    assert len([row for row in state.calls if row[0] == 'PUT']) == 1


def test_native_resource_section_edit_retains_asset_and_outside_source(native):
    state, remote, settings = native
    state.pages['11']['body']['storage']['value'] = "<h2>A</h2><ac:image><ri:attachment ri:filename='asset.png'/></ac:image><h2>B</h2><p>Outside</p>"
    state, api, replica = service(native)
    result = api.execute('update_document', dict(document='11', version=1, section='1',
        content='<ac:image><ri:attachment ri:filename="asset.png"/></ac:image><p>Added</p>'))
    assert result['status'] == 'updated'
    assert state.pages['11']['body']['storage']['value'].endswith('<h2>B</h2><p>Outside</p>')
    refused = api.execute('update_document', dict(document='11', version=2, content='<p>Remove asset</p>'))
    assert refused['status'] == 'error'
    assert len([row for row in state.calls if row[0] == 'PUT']) == 1


def test_native_footer_comment_edit_and_inline_anchor_remain_distinct(native):
    state, api, replica = service(native)
    created = api.execute('create_comment', dict(target='11', text='<p>Feedback 😀</p>', includeBody=True))
    assert created['status'] == 'created' and created['reference'] == 'footer:60'
    # The generic runner models an older comment's different original author.
    # This comment was just created by account; retain its actual v1 history.
    state.hook = lambda method, path, query, payload: (200, {}, dict(number=1, authorId='account')) if (
        method == 'GET' and path == '/wiki/api/v2/footer-comments/60/versions/1') else None
    updated = api.execute('update_comment', dict(comment='footer:60', version=1, text='<p>Revised</p>', includeBody=True))
    assert updated['status'] == 'updated' and updated['version'] == 2
    inline = api.execute('create_comment', dict(target='11', text='<p>Inline</p>', selection='Original', includeBody=True, metadata=True))
    assert inline['status'] == 'created' and inline['reference'] == 'inline:60'
    assert inline['inlineContext']['inlineOriginalSelection'] == 'Original'
    assert 'ac:inline-comment-marker' in state.pages['11']['body']['storage']['value']


def test_native_stale_version_and_private_material_perform_no_mutation(native):
    state, api, replica = service(native)
    stale = api.execute('update_document', dict(document='11', version=2, append='<p>Mine</p>'))
    assert stale['code'] == 'version_conflict'
    secret = api.execute('create_comment', dict(target='11', text='<table><tr><th>token</th><td>private-value</td></tr></table>'))
    assert secret['code'] == 'secret_detected' and 'private-value' not in str(secret)
    assert all(row[0] == 'GET' for row in state.calls)


def test_native_save_before_first_local_snapshot_is_pending_not_replayed(native):
    state, remote, settings = native
    replica = Replica(settings, remote)
    calls = []
    replica.on_repair = lambda: calls.append('repair')
    api = WikiService(settings, remote, replica)
    result = api.execute('update_document', dict(document='11', version=1, append='<p>Saved</p>'))
    assert result['status'] == 'updated' and result['syncRequired'] and calls == ['repair']
    assert not (replica.control / 'manifest.json').exists()
    assert len([row for row in state.calls if row[0] == 'PUT']) == 1


def test_whole_space_move_retains_ancestors_and_publishes_once(native, tmp_path):
    from test_remote import page_raw
    from wiki_atlassian.config import SpaceSettings
    state, remote, selected = native
    state.pages['13'] = page_raw('13', '10')
    settings = SpaceSettings(dict(site=selected.site, space='DOC', spaceId='9'),
                             selected.credentials, tmp_path / 'whole-space')
    replica = Replica(settings, remote)
    replica.synchronize()
    api = WikiService(settings, remote, replica)
    result = api.execute('update_document', dict(document='11', version=1, parent='13'))
    assert result['status'] == 'updated' and result['version'] == 2
    assert state.pages['11']['parentId'] == '13'
    assert replica.get_document(dict(document='11'))['version'] == 2
    assert len([row for row in state.calls if row[0] == 'PUT']) == 1
    refused = api.execute('update_document', dict(document='11', version=2, parent='10'))
    assert refused['status'] == 'error' and refused['code'] == 'invalid_move'
    assert len([row for row in state.calls if row[0] == 'PUT']) == 1
