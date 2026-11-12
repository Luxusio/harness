import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wiki_atlassian.markdown import to_markdown
from wiki_atlassian.storage import native_headings


@pytest.mark.parametrize('source,expected', [
    ('<p>  Same  text&nbsp; </p>', '  Same  text\u00a0 '),
    ('<p>A<br/>B</p>', 'A\nB'), ('<div><p>A</p><p>B</p></div>', 'A\n\nB'),
    ('<h2>Heading 😀</h2><p>Human <strong>bold</strong> <em>italic</em></p>', '## Heading 😀\n\nHuman **bold** *italic*'),
    ('<ul><li>First</li><li>Second</li></ul>', '- First\n- Second'),
    ('<ol><li>First</li><li>Second</li></ol>', '1. First\n2. Second'),
    ('<table><tbody><tr><th>A</th><th>B</th></tr><tr><td>x|y</td><td>z<br/>q</td></tr></tbody></table>', '| A | B |\n| --- | --- |\n| x\\|y | z<br>q |'),
    ('<p><a href="https://example.invalid/a?x=1&amp;y=2">Human</a></p>', '[Human](https://example.invalid/a?x=1&y=2)'),
    ('<p><img src="https://example.invalid/img.png" alt="Diagram"/></p>', '![Diagram](https://example.invalid/img.png)'),
    ('<ac:image><ri:attachment ri:filename="diagram.png"/></ac:image>', '![diagram.png](diagram.png)'),
    ('<ac:structured-macro ac:name="info"><ac:parameter ac:name="title">Hidden title</ac:parameter><ac:rich-text-body><p>Authored content</p></ac:rich-text-body></ac:structured-macro>', 'Authored content'),
    ('<ac:structured-macro ac:name="code"><ac:plain-text-body><![CDATA[  a < b\n  c & d]]></ac:plain-text-body></ac:structured-macro>', '```\n  a < b\n  c & d\n```'),
    ('<ac:structured-macro ac:name="jira"><ac:parameter ac:name="key">PROJ-1</ac:parameter></ac:structured-macro>', '[PROJ-1]'),
])
def test_native_markdown_projection(source, expected):
    assert to_markdown(source) == expected


def test_native_page_user_title_anchor_and_image_link_projection():
    context = 'https://wiki.example/wiki/spaces/COMMON/pages/201'
    page = '<ac:link ac:anchor="한 글"><ri:page ri:content-id="123"/><ac:plain-text-link-body><![CDATA[A [B] *C*]]></ac:plain-text-link-body></ac:link>'
    assert to_markdown(page, context) == '[A \\[B\\] \\*C\\*](https://wiki.example/wiki/spaces/COMMON/pages/123#%ED%95%9C%20%EA%B8%80)'
    assert to_markdown('<ac:link><ri:page ri:content-title="Only title" ri:space-key="OTHER"/></ac:link>', context) == 'Only title (Confluence page: title=Only title; space=OTHER; unresolved)'
    assert to_markdown('<ac:link ac:anchor="local"><ac:plain-text-link-body><![CDATA[Here]]></ac:plain-text-link-body></ac:link>', context) == '[Here](' + context + '#local)'
    assert to_markdown('<ac:link><ri:user ri:account-id="acct:123"/><ac:plain-text-link-body><![CDATA[Person]]></ac:plain-text-link-body></ac:link>', context) == '[@Person](https://wiki.example/wiki/people/acct%3A123)'
    assert to_markdown('<ac:link><ri:page ri:content-id="123"/><ac:link-body><ac:image><ri:attachment ri:filename="d.png"/></ac:image></ac:link-body></ac:link>', context) == '[![d.png](d.png)](https://wiki.example/wiki/spaces/COMMON/pages/123)'


def test_s3_image_macro_and_mapping():
    source = '<ac:structured-macro ac:name="s3-static-macro-image"><ac:parameter ac:name="path">%5B%7B%22Key%22%3A%22TMF%2Fdiagram.png%22%7D%5D</ac:parameter></ac:structured-macro>'
    assert to_markdown(source, image_path_mapping={'TMF/diagram.png': 'images/local.png'}) == '![diagram.png](images/local.png)'


def test_authored_heading_context_never_comes_from_markdown_paragraph_syntax():
    body = '<p>## Generated-looking paragraph</p><h2>Native</h2><p>Text</p>'
    assert to_markdown(body).startswith('## Generated-looking paragraph')
    assert [h['text'] for h in native_headings(body)] == ['Native']
