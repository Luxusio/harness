"""Render authored Markdown as Confluence storage, without admitting raw HTML."""
from html import escape
import re
from urllib.parse import urlsplit

from .models import RemoteError


def _code(renderer, tokens, index, options, env):
    token = tokens[index]
    language = token.info.strip().split(None, 1)
    parameter = ('<ac:parameter ac:name="language">' + escape(language[0]) + '</ac:parameter>'
                 if language else '')
    content = token.content.replace(']]>', ']]]]><![CDATA[>')
    return ('<ac:structured-macro ac:name="code">' + parameter +
            '<ac:plain-text-body><![CDATA[' + content +
            ']]></ac:plain-text-body></ac:structured-macro>\n')


def _image(renderer, tokens, index, options, env):
    token = tokens[index]
    url = token.attrGet('src') or ''
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise RemoteError('invalid_image', 'Invalid Markdown image URL.') from exc
    if parts.scheme not in ('https', 'http') or not parts.netloc:
        raise RemoteError('invalid_image', 'Markdown images require an absolute HTTP(S) URL; use native storage for attachments.')
    alt = renderer.renderInlineAsText(token.children or [], options, env)
    title = token.attrGet('title')
    title_attr = '' if title is None else ' ac:title="' + escape(title, quote=True) + '"'
    return ('<ac:image' + title_attr + ' ac:alt="' + escape(alt, quote=True) + '"><ri:url ri:value="' +
            escape(url, quote=True) + '" /></ac:image>')


def markdown_storage(text):
    if re.search(r'[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]', text):
        raise RemoteError('invalid_request', 'Markdown contains characters that XML storage cannot represent.')
    try:
        from markdown_it import MarkdownIt
    except ImportError as exc:
        raise RemoteError('dependency_unavailable', 'Markdown authoring requires markdown-it-py in the wiki MCP Python.') from exc
    parser = MarkdownIt('commonmark', {'html': False, 'xhtmlOut': True}).enable('table')
    validate = parser.validateLink

    def document_link(url):
        scheme = re.match(r'^([A-Za-z][A-Za-z0-9+.-]*):', url)
        return validate(url) and (scheme is None or scheme[1].lower() in ('http', 'https', 'mailto'))

    parser.validateLink = document_link
    parser.add_render_rule('fence', _code)
    parser.add_render_rule('code_block', _code)
    parser.add_render_rule('image', _image)
    return parser.render(text)
