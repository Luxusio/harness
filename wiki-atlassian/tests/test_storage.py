import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wiki_atlassian.models import RemoteError
from wiki_atlassian.storage import (
    apply_edits, equivalent, inline_selection, native_headings, native_page_readback,
    outline, references, replace_section, require_reference_preservation,
    retarget_links, section_range, selection_anchored, storage_section, to_storage,
)


def error(code, call):
    with pytest.raises(RemoteError) as caught:
        call()
    assert caught.value.code == code
    return caught.value


def edit(old, new):
    return {'old_text': old, 'new_text': new}


def test_native_outline_duplicate_headings_unicode_and_hidden_examples():
    body = '<p>Preamble</p><h2>Same</h2><p>First</p><h4>세부 😀</h4><p>Detail</p>' + \
        "<ac:structured-macro ac:name='expand'><ac:rich-text-body><h1>Hidden</h1></ac:rich-text-body></ac:structured-macro>" + \
        '<pre><code>&lt;h1&gt;Example&lt;/h1&gt;</code></pre><h2>Same</h2><p>Last</p>'
    assert outline(body) == '1 Same\n  1.1 세부 😀\n2 Same'
    assert storage_section(body, '2') == '<p>Last</p>'
    headings = native_headings(body)
    assert headings[1] == dict(key='1.1', level=4, text='세부 😀', start=body.index('<h4>'), end=body.index('</h4>') + 5, body_start=body.index('</h4>') + 5)


def test_sections_include_descendants_and_preserve_exact_outer_bytes():
    prefix = "<p>Preamble 😀 &amp; exact</p><h2 local-id='keep'>Same</h2><p>First</p><h2 local-id='chosen'>Same</h2>"
    suffix = "<h2>Next</h2><p><ac:inline-comment-marker ac:ref='outside'>Retain</ac:inline-comment-marker></p>"
    body = prefix + '<p>Old</p><h5>Child</h5><p>가😀나다</p>' + suffix
    assert section_range(body, '2') == (len(prefix), len(body) - len(suffix))
    assert storage_section(body, '2.1') == '<p>가😀나다</p>'
    assert replace_section(body, '2', to_storage('새 <값>& 😀', 'text')) == prefix + '<p>새 &lt;값&gt;&amp; 😀</p>' + suffix


@pytest.mark.parametrize('body', [
    '<h2>Top</h2><div><h2>Other</h2><p>Nested peer</p></div>',
    '<div><h2>Top</h2><p>Inside</p></div><p>Still Top</p><h2>Other</h2>',
    '<div><h2>Top</h2><p>Inside</p></div><p>Still Top without a following heading</p>',
    '<h2>Top</h2><p>Unclosed', '<h2>Top', '<h2>Top</h2></div>',
    '<h2>Top</h2><p x="a" x="b">Text</p>', '<!DOCTYPE h2><h2>Top</h2>',
])
def test_section_unsafe_native_ranges_rejected(body):
    error('section_context_required', lambda: section_range(body, '1'))


@pytest.mark.parametrize('key', ['0', '01', '1.0', '1..1', 'Top', '1\n', '1.' * 41])
def test_invalid_section_positions(key):
    error('invalid_section', lambda: section_range('<h2>Top</h2>', key))


@pytest.mark.parametrize('replacement', ['<p>Unclosed', '</div><p>New</p>', '<p>New</p></div>'])
def test_section_replacement_never_escapes_container(replacement):
    error('section_context_required', lambda: replace_section('<div><h2>Top</h2><p>Old</p></div>', '1', replacement))


ASSETS = [
    "<ac:image><ri:attachment ri:filename='keep.png'/></ac:image>",
    "<ac:structured-macro ac:name='note' ac:macro-id='keep'><ac:rich-text-body><p>Keep macro</p></ac:rich-text-body></ac:structured-macro>",
    "<p><ac:inline-comment-marker ac:ref='keep'>Keep comment</ac:inline-comment-marker></p>",
    "<ac:adf-extension><ac:adf-fallback><ac:structured-macro ac:name='toc' ac:macro-id='keep'/></ac:adf-fallback></ac:adf-extension>",
    "<ac:placeholder><ac:image><ri:attachment ri:filename='keep.png'/></ac:image></ac:placeholder>",
    "<ac:structured-macro ac:name='code'><ac:plain-text-body><![CDATA[<h1>Code</h1>]]></ac:plain-text-body></ac:structured-macro>",
    "<p><a name='keep'>Keep anchor</a></p>", "<h3 local-id='keep'>Keep heading anchor</h3><p>Nested</p>",
]


@pytest.mark.parametrize('asset', ASSETS)
def test_section_references_retain_fragments_and_reject_hidden_copies(asset):
    body = '<h2>Top</h2>' + asset + '<p>Old</p><h2>Other</h2>'
    for replacement in ('<p>Missing references</p>', '<pre><code>' + asset + '</code></pre>'):
        error('section_reference_removal', lambda: replace_section(body, '1', replacement))
    assert replace_section(body, '1', asset + '<p>New</p>') == '<h2>Top</h2>' + asset + '<p>New</p><h2>Other</h2>'


def test_seven_contextual_edits_use_original_disjoint_ranges():
    original = '<p>A😀</p><p>B</p><table><tbody><tr>' + ''.join('<td>' + x + '</td>' for x in 'CDEFG') + '</tr></tbody></table>'
    edits = [edit('<p>A😀</p>', '<p>B</p>'), edit('<p>B</p>', '<p>A😀 changed</p>')] + [edit('<td>' + x + '</td>', '<td>' + x + ' changed</td>') for x in 'CDEFG']
    expected = '<p>B</p><p>A😀 changed</p><table><tbody><tr>' + ''.join('<td>' + x + ' changed</td>' for x in 'CDEFG') + '</tr></tbody></table>'
    assert apply_edits(original, list(reversed(edits))) == expected


@pytest.mark.parametrize('encoded,logical', [
    ('A&middot;B', 'A·B'), ('A&#xB7;B', 'A·B'), ('A&#183;😀', 'A·😀'), ('한글 😀', '한글 😀'),
    ('One &amp; two', 'One & two'), ('A&amp;middot;B', 'A&middot;B'),
    ('&lt;b&gt;literal&lt;/b&gt;', '<b>literal</b>'), (' \tA&nbsp;  😀\nB ', ' \tA\u00a0  😀\nB '),
    ('A<br />B', 'A\nB'),
])
def test_text_edits_use_entire_decoded_leaf_and_preserve_surrounding_storage(encoded, logical):
    prefix = "<!-- byte exact -->\n<ac:structured-macro ac:name='note' ac:macro-id='untouched'><ac:parameter ac:name='title'>Keep &amp; exact</ac:parameter></ac:structured-macro><p local-id='native-p' data-keep='yes'>"
    suffix = "</p>\n<p local-id='untouched'>No  normalization &amp; no changes</p>"
    assert apply_edits(prefix + encoded + suffix, [edit(logical, '**Changed** 😀\r\n<i>literal</i> &')], 'text') == prefix + '**Changed** 😀<br />&lt;i&gt;literal&lt;/i&gt; &amp;' + suffix


@pytest.mark.parametrize('source,selection', [
    ('<p>윈스턴</p>', '스턴'), ("<p data-label='Selected'>Other</p>", 'Selected'),
    ('<p>Selected <strong>rich</strong></p>', 'Selected rich'), ('<p><![CDATA[Selected]]></p>', 'Selected'),
    ("<ac:structured-macro ac:name='code'><ac:plain-text-body><![CDATA[Selected]]></ac:plain-text-body></ac:structured-macro>", 'Selected'),
    ("<ac:structured-macro ac:name='note'><ac:parameter ac:name='title'><p>Selected</p></ac:parameter></ac:structured-macro>", 'Selected'),
    ('<script><p>Selected</p></script>', 'Selected'), ('<p>Selected<!--hidden--></p>', 'Selected'),
    ("<p>Selected<img src='untouched'/></p>", 'Selected'), ('<p>Selected<br>child</br></p>', 'Selected\nchild'),
    ('<p>Selected', 'Selected'), ('Selected', 'Selected'), ('<p>A&amp;middot;B</p>', 'A·B'),
    ('<p> Selected </p>', 'Selected'), ('<p>A&nbsp;B</p>', 'A B'), ('<p>A  B</p>', 'A B'),
    ('<p>A\nB</p>', 'A B'), ('<p>A<br/>B</p>', 'A\r\nB'),
])
def test_text_edits_refuse_fragments_rich_inert_and_normalized_content(source, selection):
    with pytest.raises(RemoteError):
        apply_edits(source, [edit(selection, 'Replacement')], 'text')


@pytest.mark.parametrize('body,old', [
    ('<p>윈스턴이 맞았습니다</p>', '스턴'), ('<p>윈<strong>스턴</strong>이 맞았습니다</p>', '<strong>스턴</strong>'),
    ("<p title='스턴'>단락 전체</p>", "title='스턴'"), ('<p>전체 문맥</p>', '<p>'),
    ('<p>전체 문맥</p>', '문맥</p>'), ('<script><p>스턴</p></script>', '<p>스턴</p>'),
])
def test_storage_fragments_attributes_and_inert_blocks_refused(body, old):
    error('patch_context_required', lambda: apply_edits(body, [edit(old, 'change')]))


def test_contextual_rich_macro_paragraphs_cells_deletion_and_entire_text():
    old = '<p>Human <strong>문맥</strong> 😀</p>'
    for prefix, suffix in [("<ac:layout><ac:layout-section ac:type='single'><ac:layout-cell>", '</ac:layout-cell></ac:layout-section></ac:layout>'), ("<ac:structured-macro ac:name='info'><ac:rich-text-body>", '</ac:rich-text-body></ac:structured-macro>')]:
        assert apply_edits(prefix + old + suffix, [edit(old, '<p>Changed</p>')]) == prefix + '<p>Changed</p>' + suffix
    assert apply_edits('<td>A😀BC</td><p>remove me</p>', [edit('A😀BC', '새 값😀'), edit('<p>remove me</p>', '')]) == '<td>새 값😀</td>'


def test_invalid_context_batches_counts_overlaps_and_newly_introduced_matches():
    body = '<div><p>A</p><p>B</p></div><p>Repeated</p><p>Repeated</p>'
    for edits in ([], [edit('A', 'B')] * 21, None, [{}], [edit('', 'new')], [dict(old_text='A', new_text=1)], [dict(old_text='A', new_text='B', extra='x')]):
        error('invalid_edit', lambda: apply_edits(body, edits))
    error('patch_not_found', lambda: apply_edits(body, [edit('<p>A</p>', '<p>Introduced</p>'), edit('<p>Introduced</p>', '<p>Chain</p>')]))
    error('patch_overlap', lambda: apply_edits(body, [edit('<div><p>A</p><p>B</p></div>', ''), edit('<p>A</p>', '')]))
    for content, old, count in [('<p>source</p>', 'missing', 0), ('<p>x x</p>', 'x', 2), ('<p>aaa</p>', 'aa', 2)]:
        caught = error('patch_not_found' if count == 0 else 'patch_ambiguous', lambda: apply_edits(content, [edit(old, 'new')]))
        assert caught.details['matchCount'] == count
    error('patch_ambiguous', lambda: apply_edits('<p>A&middot;B</p><p>A·B</p>', [edit('A·B', 'new')], 'text'))
    error('patch_overlap', lambda: apply_edits('<p>A</p>', [edit('A', 'B'), edit('A', 'C')], 'text'))


def test_reference_identity_multiplicity_hidden_examples_and_safe_diagnostics():
    image = "<ac:image ac:width='100'><ri:attachment ri:filename='private-diagram.png'><ri:page ri:content-title='Owner'/></ri:attachment></ac:image>"
    macro = "<ac:structured-macro ac:name='info' ac:macro-id='private-id'><ac:parameter ac:name='title'>Old</ac:parameter><ac:rich-text-body><p>Old</p></ac:rich-text-body></ac:structured-macro>"
    require_reference_preservation(image + macro, (image + macro).replace('100', '300').replace('Old', 'New'))
    for before, after in [(image + image, image), (macro, macro.replace('private-id', 'other')), (image, image.replace('Owner', 'Changed')),
                          (image, '<!--' + image + '-->'), (image, '<p><![CDATA[' + image + ']]></p>'),
                          (image, '<ac:plain-text-body>' + image + '</ac:plain-text-body>')]:
        caught = error('reference_removal', lambda: require_reference_preservation(before, after))
        assert caught.details['removedReferences']
        assert 'private' not in str(caught.details)
    require_reference_preservation(image, '', True)
    assert sum(references(image).values()) == 2


def test_retarget_links_edits_only_quoted_href_value_and_all_original_matches():
    body = "<!-- exact -->\n<p><a data-x='keep' href = 'https://old.invalid/?x=1&amp;y=2' title=\"keep\">Label &amp; text</a></p>" + \
        '<p><a href="https://old.invalid/?x=1&#38;y=2">Second</a></p>' + \
        "<pre><a href='https://old.invalid/?x=1&amp;y=2'>Example</a></pre>"
    result = retarget_links(body, {'https://old.invalid/?x=1&y=2': '123#A&B'}, 'https://wiki.example', 'COMMON')
    assert result == body.replace("href = 'https://old.invalid/?x=1&amp;y=2'", "href = 'https://wiki.example/wiki/spaces/COMMON/pages/123#A&amp;B'").replace('href="https://old.invalid/?x=1&#38;y=2"', 'href="https://wiki.example/wiki/spaces/COMMON/pages/123#A&amp;B"')


@pytest.mark.parametrize('body,code', [
    ("<code><a href='old'>example</a></code>", 'link_not_found'), ("<img src='old'/>", 'link_not_found'),
    ("<a href='old' HREF='other'>label</a>", 'link_context_required'), ("<a href=old>label</a>", 'link_context_required'),
    ("<a href='old'>label", 'link_context_required'),
])
def test_retarget_unsafe_and_inert_sources(body, code):
    error(code, lambda: retarget_links(body, {'old': '123'}, 'https://wiki.example', 'COMMON'))


@pytest.mark.parametrize('target', ['https://evil.invalid/wiki/spaces/COMMON/pages/123', 'https://user@wiki.example/wiki/spaces/COMMON/pages/123', '0', 'https://wiki.example/wiki/spaces/OTHER/pages/123', '123\n'])
def test_retarget_rejects_non_native_or_foreign_targets(target):
    error('invalid_link_target', lambda: retarget_links("<a href='old'>label</a>", {'old': target}, 'https://wiki.example', 'COMMON'))


def test_inline_selection_and_saved_marker_verify_exact_occurrence():
    body = '<p>same and same</p><p>Alpha beta &amp; gamma</p><p>same</p>'
    assert inline_selection(body, 'beta & gamma') == (0, 1)
    assert inline_selection(body, 'same', 3) == (2, 3)
    error('selection_ambiguous', lambda: inline_selection(body, 'same'))
    error('selection_occurrence_out_of_range', lambda: inline_selection(body, 'same', 4))
    saved = '<p>same and same</p><p><ac:inline-comment-marker ac:ref="new">same</ac:inline-comment-marker></p>'
    assert selection_anchored(saved, 'same', 2, 'new')
    assert not selection_anchored(saved, 'same', 0, 'new')
    assert not selection_anchored(saved, 'same', 2, None)
    assert not selection_anchored('<ac:macro ac:name="info">' + saved + '</ac:macro>', 'same', 2, 'new')
    assert not selection_anchored(saved + saved, 'same', 2, 'new')


@pytest.mark.parametrize('body,selection,code', [
    ('<p>Plain text</p>', 'missing', 'selection_not_found'), ('<p>Hello <strong>bold</strong> world</p>', 'Hello bold', 'selection_context_required'),
    ('<p>aaa</p>', 'aa', 'selection_context_required'),
    ('<p><![CDATA[raw]]></p><p>raw</p>', 'raw', 'selection_context_required'),
    ('<ac:task-list><ac:task><ac:task-body>task text</ac:task-body></ac:task></ac:task-list><p>task text</p>', 'task text', 'selection_context_required'),
    ('<ac:decision-list><ac:decision>decision text</ac:decision></ac:decision-list>', 'decision text', 'selection_context_required'),
    ('<p>seen <ac:inline-comment-marker ac:ref="old">term</ac:inline-comment-marker> and term</p>', 'term', 'selection_context_required'),
    ('<ac:macro ac:name="info"><ac:rich-text-body><p>hidden term</p></ac:rich-text-body></ac:macro><p>hidden term</p>', 'hidden term', 'selection_context_required'),
    ('<pre><code>literal</code></pre><p>literal</p>', 'literal', 'selection_context_required'),
    ('<p>Plain text</p>', ' Plain', 'invalid_selection'), ('<p>Plain text</p>', 'Plain\ntext', 'invalid_selection'),
    ('<p>Plain\u2028text</p>', 'Plain\u2028text', 'invalid_selection'), ('<p>Plain text</p>', '😀' * 501, 'invalid_selection'),
])
def test_inline_unsafe_selections_refused(body, selection, code):
    error(code, lambda: inline_selection(body, selection))


def test_inline_utf16_length_boundary():
    assert inline_selection('<p>' + '😀' * 500 + '</p>', '😀' * 500) == (0, 1)


def test_inline_visible_line_break_does_not_create_an_artificial_duplicate():
    assert inline_selection('<p>x<br/>y</p><p>xy</p>', 'xy') == (0, 1)
    saved = '<p>x<br/>y</p><p><ac:inline-comment-marker ac:ref="new">xy</ac:inline-comment-marker></p>'
    assert selection_anchored(saved, 'xy', 0, 'new')
    assert not selection_anchored('<p><ac:inline-comment-marker ac:ref="new">x<br/>y</ac:inline-comment-marker></p>', 'xy', 0, 'new')


@pytest.mark.parametrize('encoded,visible', [('&#9;', '\t'), ('&#10;', '\n'), ('&#13;', '\r'), ('&NewLine;', '\n'), ('&Tab;', '\t')])
def test_visible_whitespace_entities_decode_without_weakening_identity(encoded, visible):
    body = '<p>A' + encoded + 'B</p>'
    assert apply_edits(body, [edit('A' + visible + 'B', 'Changed')], 'text') == '<p>Changed</p>'
    assert not equivalent(body, '<p>A' + visible + 'B</p>')
    assert not native_page_readback(body, '<p>A' + visible + 'B</p>')


def test_numeric_entity_leading_zeroes_do_not_escape_python_integer_limits():
    for encoded in ('&#' + '0' * 4300 + '65;', '&#x' + '0' * 4300 + '41;'):
        body = '<p>' + encoded + '</p>'
        assert inline_selection(body, 'A') == (0, 1)
        assert apply_edits(body, [edit('A', 'B')], 'text') == '<p>B</p>'
        assert outline('<h2>' + encoded + '</h2>') == '1 A'
        assert equivalent(body, '<p>A</p>')
    invalid = '&#' + '9' * 5000 + ';'
    assert equivalent('<p>' + invalid + '&amp;</p>', '<p>' + invalid + '&#38;</p>')


@pytest.mark.parametrize('fragment', ['not valid', '%ZZ', '%', 'x#y', 'x<y', 'x"y', 'x\\y', 'x\u00a0y'])
def test_retarget_rejects_malformed_uri_fragments(fragment):
    error('invalid_link_target', lambda: retarget_links('<a href="old">label</a>', {'old': '123#' + fragment}, 'https://wiki.example', 'COMMON'))


def test_retarget_keeps_valid_encoded_and_unicode_fragments_exact():
    assert retarget_links('<a href="old">label</a>', {'old': '123#한글%20A?x=1&y=2'}, 'https://wiki.example', 'COMMON') == '<a href="https://wiki.example/wiki/spaces/COMMON/pages/123#한글%20A?x=1&amp;y=2">label</a>'


@pytest.mark.parametrize('path', ['/wiki/api/v2/pages/123', '/wiki/api/v2/pages/123/Title%20😀', '/wiki/spaces/COMMON/pages/123/Title%20😀'])
def test_retarget_supports_exact_native_api_and_ui_references(path):
    assert retarget_links('<a href="old">label</a>', {'old': 'https://wiki.example' + path + '#part'}, 'https://wiki.example', 'COMMON') == '<a href="https://wiki.example/wiki/spaces/COMMON/pages/123#part">label</a>'


@pytest.mark.parametrize('path', ['/wiki/spaces/COMMON/pages/123/../456', '/wiki/spaces/COMMON/pages/123/.', '/wiki/spaces/COMMON/pages/123/%2e%2e', '/wiki/spaces/COMMON/pages/123/a%2fb', '/wiki/spaces/COMMON/pages/123/a%5cb', '/wiki/spaces/COMMON/pages/123/a%252fb', '/wiki/api/v2/pages/123/%255c', '/wiki/pages/viewpage.action?pageId=123'])
def test_retarget_rejects_traversal_encoded_separators_and_non_native_routes(path):
    error('invalid_link_target', lambda: retarget_links('<a href="old">label</a>', {'old': 'https://wiki.example' + path}, 'https://wiki.example', 'COMMON'))


@pytest.mark.parametrize('left,right', [
    ('<p>A&amp;B&nbsp;C</p>', '<p>A&#38;B&#160;C</p>'),
    ('<a href="a&amp;b">A&middot;B</a>', '<a href="a&#38;b">A·B</a>'),
])
def test_serialization_equivalence_only_decodes_one_entity_layer(left, right):
    assert equivalent(left, right)
    assert native_page_readback(left, right)


@pytest.mark.parametrize('left,right', [
    ('<p>A  B</p>', '<p>A B</p>'), ('<p>A&nbsp;B</p>', '<p>A B</p>'),
    ('<p>A&amp;middot;B</p>', '<p>A·B</p>'), ('<p>&#0;</p>', '<p>�</p>'),
    ('<p>&#9;</p>', '<p>\t</p>'), ('<p>&NewLine;</p>', '<p>\n</p>'),
    ('<p><![CDATA[A&amp;B]]></p>', '<p><![CDATA[A&B]]></p>'),
    ('<p x="a">A</p>', "<p x='a'>A</p>"), ('<p><b>A</b></p>', '<p>A</p>'),
])
def test_readback_never_normalizes_human_text_or_markup(left, right):
    assert not equivalent(left, right)
    assert not native_page_readback(left, right)


def test_page_readback_tolerates_only_existing_positive_versions_and_added_native_macro_identity():
    expected = '<p><ac:link><ri:page ri:content-id="123" ri:version-at-save="1"/></ac:link></p><ac:structured-macro ac:name="info"><ac:rich-text-body><p>Human</p></ac:rich-text-body></ac:structured-macro>'
    saved = expected.replace('version-at-save="1"', 'version-at-save="2"').replace('ac:name="info"', 'ac:name="info" ac:macro-id="12345678-1234-1234-1234-123456789abc" ac:schema-version="1"')
    assert native_page_readback(saved, expected)
    assert not equivalent(saved, expected)
    for changed in (saved.replace('Human', 'Changed'), saved.replace('schema-version="1"', 'schema-version="2"'), saved.replace('12345678-1234-1234-1234-123456789abc', 'invalid'), saved.replace('ac:name="info"', 'ac:name="note"'), saved.replace('version-at-save="2"', 'version-at-save="0"')):
        assert not native_page_readback(changed, expected)
    assert not native_page_readback(expected.replace(' ri:version-at-save="1"', ''), expected)
    supplied = saved
    assert not native_page_readback(supplied.replace('12345678-1234-1234-1234-123456789abc', '22345678-1234-1234-1234-123456789abc'), supplied)


def test_plain_text_new_content_is_literal_and_storage_is_not_markdown():
    assert to_storage('A <B>&\r\nline\n\nNext **literal**', 'text') == '<p>A &lt;B&gt;&amp;<br />line</p><p>Next **literal**</p>'
    error('invalid_request', lambda: to_storage('# Heading', 'markdown'))
    error('invalid_request', lambda: to_storage(' \n', 'text'))
