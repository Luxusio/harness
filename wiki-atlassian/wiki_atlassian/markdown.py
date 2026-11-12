"""Disposable Markdown read projection of native Confluence storage."""
from __future__ import annotations

import json
import re
from urllib.parse import quote, unquote_plus, urlsplit, urlunsplit

from .storage import Element, Text, parse_storage, to_storage

_SIMPLE = set('p h1 h2 h3 h4 h5 h6 td th tr li ul ol blockquote pre div table'.split())
_CONTAINERS = {'#document', 'div', 'section', 'article', 'ac:rich-text-body', 'li', 'td', 'th', 'tr'}
_STRUCTURAL = {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr', 'pre', 'blockquote', 'ul', 'ol'}


def _simple(element):
    if element.tag not in _SIMPLE or not element.children:
        return None
    if any(not (isinstance(n, Text) and n.kind == 'text' or isinstance(n, Element) and n.tag == 'br' and not n.children) for n in element.children):
        return None
    return ''.join(n.text() if isinstance(n, Text) else '\n' for n in element.children)


def _children(element):
    nodes = element.children
    if element.tag in _CONTAINERS and any(isinstance(n, Element) for n in nodes) and _simple(element) is None:
        first, last = 0, len(nodes)
        while first < last and isinstance(nodes[first], Text) and not nodes[first].text().strip():
            first += 1
        while last > first and isinstance(nodes[last - 1], Text) and not nodes[last - 1].text().strip():
            last -= 1
        return nodes[first:last]
    return nodes


def _trim(markdown, edge, leading):
    if isinstance(edge, Element):
        if edge.tag in _STRUCTURAL:
            return markdown.removeprefix('\n') if leading else markdown.removesuffix('\n')
        if edge.tag in ('div', 'section', 'article', 'ac:rich-text-body'):
            if _simple(edge) is not None:
                return markdown
            nodes = _children(edge)
            return _trim(markdown, nodes[0] if leading and nodes else nodes[-1] if nodes else None, leading)
        if edge.tag in ('li', 'td', 'th', 'tr') and _simple(edge) is not None or edge.tag == 'br':
            return markdown
    return markdown.lstrip() if leading else markdown.rstrip()


def _label(value):
    return re.sub(r'([\[\]()*_`<>!~#])', r'\\\1', value.replace('\\', '\\\\'))


def _first(element, tag):
    return next((e for e in element.descendants() if e.tag == tag), None)


def _s3_path(value):
    try:
        parsed = json.loads(unquote_plus(value))
        key = next(item['Key'] for item in parsed if isinstance(item, dict) and isinstance(item.get('Key'), str))
        filename = re.sub(r'-+', '-', re.sub(r'\s+', '-', re.sub(r'[^a-zA-Z0-9\-_.\s]', '', key.rsplit('/', 1)[-1]))).strip('-').lower()[:100]
        return key, filename
    except (ValueError, TypeError, StopIteration):
        return None


def to_markdown(storage, current_page_url=None, image_path_mapping=None):
    root = parse_storage(storage)
    images = {} if image_path_mapping is None else image_path_mapping
    context = None
    if current_page_url:
        try:
            parsed = urlsplit(current_page_url)
            if parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password:
                context = parsed
        except ValueError:
            pass
    origin = urlunsplit((context.scheme, context.netloc, '', '', '')) if context else ''

    def content(element):
        simple = _simple(element)
        if simple is not None:
            return simple
        nodes = _children(element)
        result = convert(element)
        return _trim(_trim(result, nodes[0] if nodes else None, True), nodes[-1] if nodes else None, False)

    def convert(element):
        simple = _simple(element)
        if simple is not None:
            return simple
        return ''.join(re.sub(r'[\t\r\n\f ]+', ' ', node.text()) if isinstance(node, Text) else tag(node) for node in _children(element))

    def native_link(element):
        page = _first(element, 'ri:page')
        user = next((e for e in element.children if isinstance(e, Element) and e.tag == 'ri:user'), None)
        anchor = element.attrs.get('ac:anchor', '')
        page_id = page.attrs.get('ri:content-id', '') if page else ''
        title = page.attrs.get('ri:content-title', '') if page else ''
        space = page.attrs.get('ri:space-key', '') if page else ''
        label = next((re.sub(r'\s+', ' ', e.text()).strip() for name in ('ac:link-body', 'ac:plain-text-link-body') if (e := _first(element, name)) is not None and e.text()), None)
        label = label or title or anchor or (element.text() if page is None else f'Confluence page {page_id}')
        image_body = _first(element, 'ac:link-body')
        image_body = image_body if image_body is not None and any(e.tag in ('ac:image', 'img') or e.tag == 'ac:structured-macro' and e.attrs.get('ac:name') == 's3-static-macro-image' for e in image_body.descendants()) else None
        rendered = convert(image_body).strip() if image_body is not None else _label(label)
        fragment = '#' + quote(anchor, safe='') if anchor else ''
        if page is None and user is not None:
            def bounded(name):
                value = user.attrs.get(name, '')
                return value if value.strip() and len(value) <= 500 and not any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value) else None
            account = bounded('ri:account-id')
            if account:
                return f'[@{_label(label or account)}]({origin}/wiki/people/{quote(account, safe="")})'
            legacy = [name.removeprefix('ri:') + '=' + value for name in ('ri:userkey', 'ri:username') if (value := bounded(name))]
            return ('@' + _label(label) + ' ' if label else '') + '(Confluence user: ' + (_label('; '.join(legacy)) + '; unresolved' if legacy else 'unresolved') + ')'
        if page is not None and re.fullmatch(r'[1-9][0-9]*', page_id):
            current_space = re.fullmatch(r'/wiki/spaces/([^/]+)/pages/[1-9][0-9]*(?:/.*)?', context.path) if context else None
            selected_space = quote(space, safe='') if space else current_space[1] if current_space else None
            path = f'/wiki/spaces/{selected_space}/pages/{page_id}' if selected_space else f'/wiki/pages/viewpage.action?pageId={page_id}'
            return f'[{rendered}]({origin}{path}{fragment})'
        if page is None:
            current = urlunsplit((context.scheme, context.netloc, context.path, context.query, '')) if context else ''
            return f'[{rendered}]({current}{fragment})' if anchor else rendered
        details = '; '.join(name + '=' + value for name, value in [('title', title), ('id', page_id), ('space', space), ('anchor', anchor)] if value)
        return f'{rendered} (Confluence page: {_label(details)}; unresolved)'

    def tag(element):
        name = element.tag
        # Handle native links before descending, including their literal CDATA labels.
        if name == 'ac:link':
            return native_link(element)
        if name in ('ac:structured-macro', 'ac:macro'):
            macro = element.attrs.get('ac:name', '')
            if macro in ('code', 'noformat'):
                body = _first(element, 'ac:plain-text-body')
                return '\n```\n' + (body.text() if body else '') + '\n```\n'
            if macro == 's3-static-macro-image':
                parameter = next((e for e in element.descendants() if e.tag == 'ac:parameter' and e.attrs.get('ac:name') == 'path'), None)
                resource = _s3_path(parameter.text()) if parameter else None
                return f'![{resource[1]}]({images.get(resource[0], resource[0])})' if resource else ''
            if macro == 'jira':
                parameter = next((e for e in element.descendants() if e.tag == 'ac:parameter' and e.attrs.get('ac:name') == 'key'), None)
                return '[' + parameter.text() + ']' if parameter and parameter.text() else ''
            if macro in ('panel', 'info', 'note', 'warning', 'tip'):
                return convert(_first(element, 'ac:rich-text-body') or element)
            return convert(element)
        if name == 'ac:parameter':
            return ''
        value = convert(element)
        if re.fullmatch(r'h[1-6]', name):
            return '\n' + '#' * int(name[1]) + ' ' + value + '\n'
        if name == 'p':
            return '\n' + value + '\n'
        if name == 'br':
            return '\n'
        if name == 'hr':
            return '\n---\n'
        if name in ('strong', 'b'):
            return '**' + value + '**'
        if name in ('em', 'i'):
            return '*' + value + '*'
        if name == 'code':
            return '`' + value + '`'
        if name == 'pre':
            return '\n```\n' + value + '\n```\n'
        if name == 'blockquote':
            return '\n> ' + value.replace('\n', '\n> ') + '\n'
        if name == 'a':
            return '[' + value + '](' + element.attrs['href'] + ')' if element.attrs.get('href') else value
        if name == 'img':
            source = element.attrs.get('src', '')
            return '![' + (element.attrs.get('alt') or 'image') + '](' + images.get(source, source) + ')' if source else ''
        if name == 'ac:image':
            attachment = _first(element, 'ri:attachment')
            filename = attachment.attrs.get('ri:filename', '') if attachment else ''
            return f'![{filename}]({images.get(filename, filename)})' if filename else value
        if name in ('ul', 'ol'):
            items = [e for e in element.children if isinstance(e, Element) and e.tag == 'li']
            return '\n' + '\n'.join((f'{i}. ' if name == 'ol' else '- ') + content(e) for i, e in enumerate(items, 1)) + '\n'
        if name == 'table':
            rows = [e for e in element.descendants() if e.tag == 'tr']
            result = []
            for index, row in enumerate(rows):
                cells = [content(e).replace('|', '\\|').replace('\r', '').replace('\n', '<br>') for e in row.descendants() if e.tag in ('td', 'th')]
                result.append('| ' + ' | '.join(cells) + ' |')
                if index == 0:
                    result.append('| ' + ' | '.join('---' for _ in cells) + ' |')
            return '\n' + '\n'.join(result) + ('\n' if result else '') + '\n'
        return value

    return content(root)
