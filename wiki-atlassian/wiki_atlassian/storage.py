"""Positioned native storage operations. Parsed nodes are never serialized for writes."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from html import escape
from html.entities import html5
import re
from urllib.parse import quote, unquote, urlsplit

from .models import RemoteError

_NAME = re.compile(r'[A-Za-z_:][A-Za-z0-9_.:-]*')
_ENTITY = re.compile(r'&(?:#[0-9]+|#[xX][0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]+);')
_WS = ' \t\r\n'
_BLOCKS = set('p h1 h2 h3 h4 h5 h6 td th tr li ul ol blockquote pre div table'.split())
_LITERAL = set('ac:plain-text-body ac:parameter script style textarea title xmp iframe noembed noframes plaintext'.split())
_LINK_LITERAL = _LITERAL | {'code', 'pre'}
_UNSELECTABLE = _LINK_LITERAL | set('ac:structured-macro ac:macro ac:inline-comment-marker ac:adf-extension ac:task-list ac:task ac:task-body ac:task-id ac:task-uuid ac:task-status ac:decision-list ac:decision ac:placeholder'.split())


def _error(code, message, **details):
    raise RemoteError(code, message, details=details, next='Read the current native storage and use an exact complete context.')


def decode_entities(value, visible=False):
    """Decode one layer; identity keeps XML line-whitespace references distinct."""
    def decode(match):
        raw = match[0]
        if raw.startswith('&#'):
            digits = raw[2:-1]
            hexadecimal = digits[:1].lower() == 'x'
            significant = (digits[1:] if hexadecimal else digits).lstrip('0') or '0'
            # Invalid references remain literal, including arbitrarily long numbers.
            if len(significant) > (6 if hexadecimal else 7):
                return raw
            number = int(significant, 16 if hexadecimal else 10)
            valid = 0x20 <= number <= 0xD7FF or 0xE000 <= number <= 0xFFFD or 0x10000 <= number <= 0x10FFFF
            return chr(number) if valid or visible and number in (9, 10, 13) else raw
        decoded = html5.get(raw[1:])
        return decoded if decoded is not None and (visible or not any(c in '\t\r\n' for c in decoded)) else raw
    return _ENTITY.sub(decode, value)


@dataclass(eq=False)
class Text:
    data: str
    start: int
    end: int
    kind: str = 'text'
    parent: Element | None = None

    def text(self):
        return decode_entities(self.data, visible=True) if self.kind == 'text' else self.data if self.kind == 'cdata' else ''


@dataclass(eq=False)
class Element:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)
    parent: Element | None = None
    start: int = 0
    open_end: int = 0
    close_start: int = 0
    end: int = 0
    self_closing: bool = False
    attribute_ranges: dict = field(default_factory=dict)

    def ancestors(self):
        parent = self.parent
        while parent is not None:
            yield parent
            parent = parent.parent

    def descendants(self):
        # Iterative traversal also handles legitimately deep native layouts.
        stack = list(reversed(self.children))
        while stack:
            node = stack.pop()
            if isinstance(node, Element):
                yield node
                stack.extend(reversed(node.children))

    def text(self):
        result = []
        stack = list(reversed(self.children))
        while stack:
            node = stack.pop()
            if isinstance(node, Text):
                result.append(node.text())
            elif node.tag == 'br':
                result.append('\n')
            else:
                stack.extend(reversed(node.children))
        return ''.join(result)


def _opening(body, start):
    """Read a single tag, retaining each quoted attribute value and separator span."""
    name = _NAME.match(body, start + 1)
    if name is None:
        raise ValueError('tag')
    raw_name = name[0]
    cursor = name.end()
    attrs, ranges = {}, {}
    while cursor < len(body):
        separator = cursor
        while cursor < len(body) and body[cursor] in _WS:
            cursor += 1
        if body.startswith('/>', cursor):
            return raw_name, attrs, ranges, cursor + 2, True
        if body.startswith('>', cursor):
            return raw_name, attrs, ranges, cursor + 1, False
        if cursor == separator:
            raise ValueError('separator')
        attribute = _NAME.match(body, cursor)
        if attribute is None:
            raise ValueError('attribute')
        key = attribute[0].lower()
        if key in attrs:
            raise ValueError('duplicate')
        cursor = attribute.end()
        while cursor < len(body) and body[cursor] in _WS:
            cursor += 1
        if cursor >= len(body) or body[cursor] != '=':
            raise ValueError('equals')
        cursor += 1
        while cursor < len(body) and body[cursor] in _WS:
            cursor += 1
        if cursor >= len(body) or body[cursor] not in "\"'":
            raise ValueError('quote')
        delimiter = body[cursor]
        value_start = cursor + 1
        value_end = body.find(delimiter, value_start)
        if value_end < 0 or '<' in body[value_start:value_end]:
            raise ValueError('value')
        attrs[key] = decode_entities(body[value_start:value_end], visible=True)
        ranges[key] = (value_start, value_end, separator, value_end + 1)
        cursor = value_end + 1
    raise ValueError('unclosed')


def parse_storage(body, error_code='storage_context_required'):
    """Parse only explicit native ranges; reject repairs, dropped tags and DTDs."""
    if not isinstance(body, str):
        _error(error_code, 'Native storage must be a string')
    root = Element('#document', end=len(body), close_start=len(body))
    stack = [(root, '#document')]
    cursor = 0
    try:
        while cursor < len(body):
            parent = stack[-1][0]
            if body[cursor] != '<':
                until = body.find('<', cursor)
                until = len(body) if until < 0 else until
                parent.children.append(Text(body[cursor:until], cursor, until, parent=parent))
                cursor = until
            elif body.startswith('<!--', cursor) or body.startswith('<![CDATA[', cursor) or body.startswith('<?', cursor):
                opening, ending, kind = ('<!--', '-->', 'comment') if body.startswith('<!--', cursor) else ('<![CDATA[', ']]>', 'cdata') if body.startswith('<![CDATA[', cursor) else ('<?', '?>', 'instruction')
                until = body.find(ending, cursor + len(opening))
                if until < 0:
                    raise ValueError('literal')
                parent.children.append(Text(body[cursor + len(opening):until], cursor, until + len(ending), kind, parent))
                cursor = until + len(ending)
            elif body.startswith('</', cursor):
                match = _NAME.match(body, cursor + 2)
                if match is None or len(stack) == 1 or match[0] != stack[-1][1]:
                    raise ValueError('closing')
                until = match.end()
                while until < len(body) and body[until] in _WS:
                    until += 1
                if until >= len(body) or body[until] != '>':
                    raise ValueError('closing')
                element, _ = stack.pop()
                element.close_start, element.end = cursor, until + 1
                cursor = until + 1
            else:
                name, attrs, ranges, until, self_closing = _opening(body, cursor)
                element = Element(name.lower(), attrs, parent=parent, start=cursor, open_end=until,
                                  close_start=until, end=until, self_closing=self_closing, attribute_ranges=ranges)
                parent.children.append(element)
                if not self_closing:
                    stack.append((element, name))
                cursor = until
        if len(stack) != 1:
            raise ValueError('unclosed')
    except (ValueError, OverflowError):
        _error(error_code, 'Malformed storage cannot provide exact native source ranges')
    return root


def _hidden(element, tags):
    return any(parent.tag in tags for parent in element.ancestors())


def _headings(root):
    ancestors, counts, headings = [], Counter(), []
    for element in root.descendants():
        if not re.fullmatch(r'h[1-6]', element.tag) or _hidden(element, _UNSELECTABLE):
            continue
        level = int(element.tag[1])
        while ancestors and ancestors[-1][0] >= level:
            ancestors.pop()
        parent = ancestors[-1][1] if ancestors else ''
        counts[parent] += 1
        key = (parent + '.' if parent else '') + str(counts[parent])
        ancestors.append((level, key))
        headings.append((key, element))
    return headings


def native_headings(body):
    return [dict(key=key, level=int(e.tag[1]), text=re.sub(r'\s+', ' ', e.text()).strip(),
                 start=e.start, end=e.end, body_start=e.end)
            for key, e in _headings(parse_storage(body, 'section_context_required'))]


def outline(body):
    return '\n'.join('  ' * item['key'].count('.') + item['key'] + ' ' + item['text'] for item in native_headings(body))


def section_range(body, key):
    if not isinstance(key, str) or len(key) > 80 or re.fullmatch(r'[1-9][0-9]*(\.[1-9][0-9]*)*', key) is None:
        _error('invalid_section', 'Use an exact section position returned by format:outline')
    root = parse_storage(body, 'section_context_required')
    headings = _headings(root)
    heading = next((e for position, e in headings if position == key), None)
    if heading is None:
        _error('section_not_found', 'Section position is absent in this document version')
    level = int(heading.tag[1])
    siblings = heading.parent.children
    following = siblings[siblings.index(heading) + 1:]
    next_sibling = next((e for e in following if isinstance(e, Element) and re.fullmatch(r'h[1-6]', e.tag) and int(e.tag[1]) <= level), None)
    global_next = next((e for _, e in headings if e.start >= heading.end and int(e.tag[1]) <= level), None)
    if next_sibling is not global_next or (heading.parent is not root and next_sibling is None and body[heading.parent.end:].strip()):
        _error('section_context_required', 'Section crosses native containers; use a complete contextual edit')
    return heading.end, next_sibling.start if next_sibling else heading.parent.close_start


def storage_section(body, key):
    start, end = section_range(body, key)
    return body[start:end]


def _section_references(body):
    root = parse_storage(body, 'section_context_required')
    excluded = _LINK_LITERAL | {'ac:structured-macro', 'ac:macro', 'ac:adf-extension', 'ac:placeholder'}
    result = Counter()
    for element in root.descendants():
        retained = element.tag in {'ac:image', 'ri:attachment', 'ac:structured-macro', 'ac:macro', 'ac:adf-extension', 'ac:placeholder', 'ac:inline-comment-marker'} or any(name in element.attrs for name in ('id', 'local-id', 'ac:local-id')) or element.tag == 'a' and 'name' in element.attrs
        if retained and not _hidden(element, excluded):
            result[tuple(_identity_tokens(body[element.start:element.end]))] += 1
    return result


def replace_section(body, key, replacement):
    start, end = section_range(body, key)
    previous, fresh = _section_references(body[start:end]), _section_references(replacement)
    if previous - fresh:
        _error('section_reference_removal', 'Section replacement would change or remove an image, attachment, macro, anchor or inline comment')
    return body[:start] + replacement + body[end:]


def _inline_text(text):
    return '<br />'.join(escape(part, quote=False) for part in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'))


def to_storage(text, format='storage'):
    if not isinstance(text, str) or format not in ('storage', 'text'):
        _error('invalid_request', 'Use storage or text for new content')
    if format == 'storage':
        return text
    if not text.strip():
        _error('invalid_request', 'New text must not be blank')
    return ''.join('<p>' + _inline_text(part) + '</p>' for part in text.replace('\r\n', '\n').replace('\r', '\n').split('\n\n'))


def _patches(body, patches, code='patch_overlap'):
    patches.sort(key=lambda item: item[0])
    if any(left[1] > right[0] for left, right in zip(patches, patches[1:])):
        _error(code, 'Edit ranges must not overlap')
    for start, end, replacement in reversed(patches):
        body = body[:start] + replacement + body[end:]
    return body


def _occurrences(text, selection, overlapping=False):
    count, at = 0, text.find(selection)
    while at >= 0:
        count += 1
        at = text.find(selection, at + (1 if overlapping else len(selection)))
    return count


def apply_edits(body, edits, format='storage'):
    if format not in ('storage', 'text') or not isinstance(edits, list) or not 1 <= len(edits) <= 20:
        _error('invalid_edit', 'edits must contain 1..20 old_text/new_text objects')
    root = parse_storage(body, 'patch_context_required')
    blocks = [e for e in root.descendants() if e.tag in _BLOCKS and not e.self_closing and not _hidden(e, _LITERAL)]
    simple = [(e, ''.join(child.text() if isinstance(child, Text) else '\n' for child in e.children)) for e in blocks if e.children and all(isinstance(child, Text) and child.kind == 'text' or isinstance(child, Element) and child.tag == 'br' and not child.children for child in e.children)]
    patches = []
    for index, edit in enumerate(edits):
        if not isinstance(edit, dict) or set(edit) != {'old_text', 'new_text'} or not all(isinstance(v, str) for v in edit.values()) or not edit['old_text']:
            _error('invalid_edit', 'Each edit must contain exactly nonempty old_text and new_text strings', editIndex=index)
        old, new = edit['old_text'], edit['new_text']
        if format == 'text':
            matches = [e for e, text in simple if text == old]
            count = len(matches)
            if count != 1:
                _error('patch_not_found' if count == 0 else 'patch_ambiguous', 'old_text must match exactly one entire simple block text', matchCount=count, editIndex=index)
            element = matches[0]
            patches.append((element.open_end, element.close_start, _inline_text(new)))
        else:
            count = _occurrences(body, old, True)
            if count != 1:
                _error('patch_not_found' if count == 0 else 'patch_ambiguous', 'old_text must match exactly once in current storage', matchCount=count, editIndex=index)
            start = body.find(old)
            end = start + len(old)
            complete = any((e.start == start and e.end == end) or (e.children and all(isinstance(c, Text) and c.kind in ('text', 'cdata') for c in e.children) and e.open_end == start and e.close_start == end) for e in blocks)
            if not complete:
                _error('patch_context_required', 'old_text must select a complete block or its entire plain text', editIndex=index)
            patches.append((start, end, new))
    return _patches(body, patches)


def references(body):
    root = parse_storage(body)
    result = Counter()
    def attachment(element):
        values = ['attachment'] + [element.attrs.get(key, '') for key in ('ri:filename', 'ri:content-id', 'ri:content-title', 'ri:space-key')]
        resources = sorted((e.tag,) + tuple(e.attrs.get(key, '') for key in ('ri:content-id', 'ri:content-title', 'ri:space-key')) for e in element.children if isinstance(e, Element) and e.tag in ('ri:page', 'ri:space'))
        return tuple(values) + tuple(value for resource in resources for value in resource)
    for e in root.descendants():
        if _hidden(e, _LITERAL):
            continue
        if e.tag == 'ri:attachment':
            result['attachments', attachment(e)] += 1
        elif e.tag == 'ac:image':
            target = next((c for c in e.children if isinstance(c, Element) and c.tag in ('ri:attachment', 'ri:url')), None)
            identity = ('unresolved',) if target is None else attachment(target) if target.tag == 'ri:attachment' else ('url', target.attrs.get('ri:value', ''))
            result['images', identity] += 1
        elif e.tag in ('ac:structured-macro', 'ac:macro'):
            result['macros', (e.attrs.get('ac:name', ''), e.attrs.get('ac:macro-id', ''))] += 1
    return result


def require_reference_preservation(before, after, allow_removal=False):
    if allow_removal:
        return
    removed = Counter()
    for (kind, _), count in (references(before) - references(after)).items():
        removed[kind] += count
    if removed:
        _error('reference_removal', 'Whole-body replacement would remove existing images, attachments or macros', removedReferences=dict(removed))


def retarget_links(body, links, origin, space=None):
    if not isinstance(links, dict) or not 1 <= len(links) <= 20 or any(not isinstance(k, str) or not k.strip() or len(k) > 4096 or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in k) or not isinstance(v, str) for k, v in links.items()):
        _error('invalid_links', 'links must contain 1..20 exact href to document-reference strings')
    root = parse_storage(body, 'link_context_required')
    found = {key: [] for key in links}
    for element in root.descendants():
        if element.tag == 'a' and not _hidden(element, _LINK_LITERAL) and element.attrs.get('href') in found:
            found[element.attrs['href']].append(element.attribute_ranges['href'][:2])
    if any(not values for values in found.values()):
        _error('link_not_found', 'Every links key must match an existing document link', missingCount=sum(not v for v in found.values()))
    base = urlsplit(origin)
    if base.scheme != 'https' or not base.hostname or base.username or base.password or base.path not in ('', '/') or base.query or base.fragment:
        _error('invalid_link_target', 'Use an exact same-origin native document reference')
    patches = []
    for old, reference in links.items():
        try:
            if len(reference) > 4096 or any(c.isspace() or ord(c) < 32 or 127 <= ord(c) <= 159 or c in '<>"{}|\\^`' for c in reference) or re.search(r'%(?![0-9A-Fa-f]{2})', reference):
                raise ValueError()
            target, separator, fragment = reference.partition('#')
            if len(fragment) > 3000 or '#' in fragment:
                raise ValueError()
            selected_space = space
            if re.fullmatch(r'[1-9][0-9]*', target):
                page_id = target
            else:
                parsed = urlsplit(target)
                if not target.startswith('https://') or parsed.hostname != base.hostname or parsed.port != base.port or parsed.username or parsed.password or parsed.query:
                    raise ValueError()
                match = re.fullmatch(r'/wiki/spaces/([A-Za-z0-9_-]+)/pages/([1-9][0-9]*)(?:/([^/]*))?', parsed.path)
                api = re.fullmatch(r'/wiki/api/v2/pages/([1-9][0-9]*)(?:/([^/]*))?', parsed.path)
                if match:
                    if space is not None and match[1] != space:
                        raise ValueError()
                    selected_space, page_id = match[1], match[2]
                    slug = match[3] or ''
                elif api:
                    page_id, slug = api[1], api[2] or ''
                else:
                    raise ValueError()
                decoded = unquote(slug)
                if decoded in ('.', '..') or any(c in '/\\' or ord(c) < 32 or 127 <= ord(c) <= 159 for c in decoded) or re.search(r'%2e|%2f|%5c', decoded, re.IGNORECASE):
                    raise ValueError()
            href = origin.rstrip('/') + (f'/wiki/spaces/{quote(selected_space, safe="%")}/pages/{page_id}' if selected_space else f'/wiki/pages/viewpage.action?pageId={page_id}') + ('#' + fragment if separator else '')
            replacement = escape(href, quote=True).replace('&#x27;', '&apos;')
            patches.extend((start, end, replacement) for start, end in found[old])
        except (ValueError, TypeError):
            _error('invalid_link_target', 'Use an exact native document ID or same-origin page URL with an optional fragment')
    return _patches(body, patches, 'link_context_required')


def check_selection(selection):
    if not isinstance(selection, str) or not selection.strip() or len(selection.encode('utf-16-le', errors='surrogatepass')) > 2000 or selection != selection.strip() or any(ord(c) < 32 or 127 <= ord(c) <= 159 or c in '\u2028\u2029' for c in selection):
        _error('invalid_selection', 'selection must be exact single-line page text without surrounding whitespace, at most 1000 UTF-16 units')


def _selectable_matches(root, selection, stop_at=None):
    count = 0
    stack = list(reversed(root.children))
    while stack:
        node = stack.pop()
        if node is stop_at:
            break
        if isinstance(node, Text):
            if node.kind == 'text':
                count += _occurrences(node.text(), selection)
        elif node.tag not in _UNSELECTABLE:
            stack.extend(reversed(node.children))
    return count


def inline_selection(body, selection, occurrence=None):
    check_selection(selection)
    root = parse_storage(body, 'selection_context_required')
    count = _selectable_matches(root, selection)
    if count != _occurrences(root.text(), selection, True):
        _error('selection_context_required', 'selection crosses formatting, overlaps itself or appears in hidden or inert content', matchCount=count)
    if count == 0:
        _error('selection_not_found', 'selection does not occur in current page text', matchCount=count)
    if occurrence is None and count > 1:
        _error('selection_ambiguous', 'selection occurs more than once; pass occurrence', matchCount=count)
    if occurrence is not None and (type(occurrence) is not int or not 1 <= occurrence <= count):
        _error('selection_occurrence_out_of_range', 'occurrence must identify an existing match', matchCount=count)
    return (1 if occurrence is None else occurrence) - 1, count


def selection_anchored(body, selection, index, marker_ref):
    if marker_ref is None:
        return False
    try:
        root = parse_storage(body)
        markers = [e for e in root.descendants() if e.tag == 'ac:inline-comment-marker' and e.attrs.get('ac:ref') == marker_ref]
        return len(markers) == 1 and not _hidden(markers[0], _UNSELECTABLE) and markers[0].text() == selection and _selectable_matches(root, selection, markers[0]) == index
    except RemoteError:
        return False


def _identity_tokens(value, page_versions=False):
    result, index = [], 0
    while index < len(value):
        if value[index] != '<':
            end = value.find('<', index)
            end = len(value) if end < 0 else end
            result.append(('text', decode_entities(value[index:end])))
            index = end
            continue
        special = '-->' if value.startswith('<!--', index) else ']]>' if value.startswith('<![CDATA[', index) else '?>' if value.startswith('<?', index) else None
        if special:
            end = value.find(special, index + 2)
            if end < 0:
                raise ValueError('literal')
            result.append(('literal', value[index:end + len(special)]))
            index = end + len(special)
            continue
        if value.startswith('<!', index):
            raise ValueError('declaration')
        page = page_versions and re.match(r'<ri:page(?=[ \t\r\n>])', value[index:]) is not None
        part, cursor = index, index + 1
        while cursor < len(value) and value[cursor] != '>':
            if value[cursor] in "\"'":
                delimiter = value[cursor]
                result.append(('markup', value[part:cursor + 1]))
                end = value.find(delimiter, cursor + 1)
                if end < 0 or '<' in value[cursor + 1:end]:
                    raise ValueError('attribute')
                normalized = decode_entities(value[cursor + 1:end])
                native_version = page and re.search(r'(?:^|[ \t\r\n])ri:version-at-save[ \t\r\n]*=[ \t\r\n]*$', value[part:cursor]) and re.fullmatch(r'[1-9][0-9]*', normalized)
                result.append(('native-page-version', '') if native_version else ('attribute', normalized))
                part, cursor = end, end + 1
            else:
                cursor += 1
        if cursor >= len(value):
            raise ValueError('unclosed')
        result.append(('markup', value[part:cursor + 1]))
        index = cursor + 1
    return result


def equivalent(left, right):
    if left == right:
        return True
    try:
        return _identity_tokens(left) == _identity_tokens(right)
    except (ValueError, OverflowError):
        return False


def native_page_readback(saved, requested):
    if saved == requested:
        return True
    try:
        actual, expected = _identity_tokens(saved, True), _identity_tokens(requested, True)
        if actual == expected:
            return True
        actual_root, expected_root = parse_storage(saved), parse_storage(requested)
        actual_macros = [e for e in actual_root.descendants() if e.tag == 'ac:structured-macro']
        expected_macros = [e for e in expected_root.descendants() if e.tag == 'ac:structured-macro']
        if not actual_macros or len(actual_macros) != len(expected_macros):
            return False
        patches = []
        for a, e in zip(actual_macros, expected_macros):
            if 'ac:name' not in a.attrs or 'ac:name' not in e.attrs:
                return False
            for name in a.attrs.keys() - e.attrs.keys():
                if not (name == 'ac:macro-id' and re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', a.attrs[name]) or name == 'ac:schema-version' and a.attrs[name] == '1'):
                    return False
                _, _, start, end = a.attribute_ranges[name]
                patches.append((start, end, ''))
        return _identity_tokens(_patches(saved, patches), True) == expected
    except (RemoteError, ValueError, OverflowError):
        return False


def to_markdown(storage, current_page_url=None):
    from .markdown import to_markdown as convert
    return convert(storage, current_page_url=current_page_url)
