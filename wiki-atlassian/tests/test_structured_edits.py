from test_remote import native
from test_service_native import service
from test_storage import error
from wiki_atlassian.storage import apply_edits, parse_storage, preserve_code_ids, require_reference_preservation


def code(literal, macro_id='', title=''):
    identity = f' ac:macro-id="{macro_id}"' if macro_id else ''
    parameter = f'<ac:parameter ac:name="title">{title}</ac:parameter>' if title else ''
    return (f'<ac:structured-macro ac:name="code"{identity}>' + parameter +
            f'<ac:plain-text-body><![CDATA[{literal}]]></ac:plain-text-body></ac:structured-macro>')


def test_heading_positions_titles_duplicates_and_protected_source_ranges():
    body = '<h2>첫 제목</h2><p>old</p><h2>두 번째</h2><p>outside</p>'
    edits = [dict(op='insert_after_heading', section='1', content='<p>new</p>'),
             dict(op='replace_section', section='1', content='<p>corrected</p>'),
             dict(op='insert_before_heading', heading='두 번째', content='<h2>중간</h2>')]
    assert apply_edits(body, edits) == '<h2>첫 제목</h2><p>new</p><p>corrected</p><h2>중간</h2><h2>두 번째</h2><p>outside</p>'
    marked = apply_edits(body, [dict(edits[0], content='**근거**')], 'markdown')
    assert '<h2>첫 제목</h2><p><strong>근거</strong></p>' in marked
    error('patch_ambiguous', lambda: apply_edits(body + body, [edits[-1]]))
    error('section_context_required', lambda: apply_edits(body, [dict(edits[-1], content='</div>')]))
    error('section_reference_removal', lambda: apply_edits(body.replace('old', '<ri:attachment ri:filename="x"/>'), [edits[1]]))


def test_entity_spelling_equivalence_never_decodes_a_second_layer_or_literal_code():
    old = '<p>A &middot; <strong>B &#x2014; C</strong></p>'
    selection = '<p>A · <strong>B — C</strong></p>'
    edit = dict(old_text=selection, new_text='<p>changed</p>')
    assert apply_edits(old, [edit]) == edit['new_text']
    error('patch_ambiguous', lambda: apply_edits(old + selection, [edit]))
    error('patch_not_found', lambda: apply_edits(old.replace('&middot;', '&amp;middot;'), [edit]))
    error('patch_not_found', lambda: apply_edits('<p><![CDATA[A&middot;B]]></p>', [dict(old_text='<![CDATA[A·B]]>', new_text='NO')]))


def test_literal_edits_share_one_snapshot_and_escape_cdata_terminator():
    first, second = code('line one &amp;\nline two', 'keep'), code('line one', 'other')
    change = dict(op='replace_code', macro_id='keep', old_text='line one', new_text='x]]>y')
    result = apply_edits(first + second, [change, dict(change, old_text='line two', new_text='last')])
    nodes = [n for n in parse_storage(result).descendants() if n.tag == 'ac:plain-text-body']
    assert [n.text() for n in nodes] == ['x]]>y &amp;\nlast', 'line one']
    assert result.endswith(second) and 'ac:macro-id="keep"' in result
    error('patch_ambiguous', lambda: apply_edits(first + second, [{k:v for k,v in change.items() if k != 'macro_id'}]))
    error('patch_overlap', lambda: apply_edits(first, [change, change]))


def test_id_reconciliation_requires_evidence_and_keeps_reference_protection():
    before = code('a', 'A') + code('original', 'B', title='Named')
    after = code('changed', title='Named') + code('a')
    result = preserve_code_ids(before, after)
    assert result == code('changed', 'B', 'Named') + code('a', 'A')
    require_reference_preservation(before, result)
    for old, new in [(code('a', 'A') + code('a', 'B'), code('a') + code('a')),
                     (code('a', 'A'), code('b')), (code('a', 'A'), code('a', 'different')),
                     (code('a', 'A'), '<ac:structured-macro ac:name="note"/>' + code('b'))]:
        error('reference_removal', lambda: require_reference_preservation(old, preserve_code_ids(old, new)))


def test_versioned_native_heading_code_and_whole_body_edits_publish_once(native):
    state, _, _ = native
    initial = '<h2>Start</h2>' + code('old', 'A') + '<ac:image><ri:attachment ri:filename="x.png"/></ac:image>'
    state.pages['11']['body']['storage']['value'] = initial
    state, api, _ = service(native)
    edits = [dict(op='insert_after_heading', heading='Start', content='<p>new</p>'),
             dict(op='replace_code', macro_id='A', old_text='old', new_text='corrected')]
    result = api.execute('update_document', dict(document='11', version=1, edits=edits))
    assert result['status'] == 'updated' and result['version'] == 2
    current = state.pages['11']['body']['storage']['value']
    assert '<h2>Start</h2><p>new</p>' in current and '<![CDATA[corrected]]>' in current
    revised = '<p>Intro</p>' + current.replace(' ac:macro-id="A"', '')
    assert api.execute('update_document', dict(document='11', version=2, content=revised))['status'] == 'updated'
    assert state.pages['11']['body']['storage']['value'] == '<p>Intro</p>' + current
    assert api.execute('update_document', dict(document='11', version=3, content=code('corrected')))['code'] == 'reference_removal'
    assert sum(row[0] == 'PUT' for row in state.calls) == 2
