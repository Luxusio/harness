"""Reject authentication material in native text and credential tables."""
from __future__ import annotations

import re

from .models import RemoteError
from .storage import Element, Text, parse_storage


PATTERNS = tuple(map(re.compile, (
    r'(?i)Authorization\s*:\s*Bearer\s+[^\s<>]{8,}',
    r'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----',
    r'(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])',
    r'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|ATATT[A-Za-z0-9_=-]{20,}|sk-[A-Za-z0-9_-]{20,})',
    r'eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}',
    r'''(?i)(?:password|passwd|api[_ -]?key|(?:access[_ -]?)?token|secret|비밀번호)\s*[:=]\s*["']?(?!<|\$\{|환경|없음|REDACTED|redacted|placeholder|example|\*{3})[^\s"'<>]{4,}''',
)))
FIELDS = {'password': 'password', 'passwd': 'password', '비밀번호': 'password',
          'api key': 'api_key', 'token': 'token', 'access token': 'token',
          'secret': 'secret', 'private key': 'private_key'}
BLOCKS = set('p div h1 h2 h3 h4 h5 h6 table tr td th li ul ol pre blockquote br'.split())


def _visible(element):
    # Inline elements join their text; block boundaries supply spaces, as in
    # native XML text projection. CDATA code remains authoritative input.
    result = []
    stack = list(reversed(element.children))
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            result.append(node)
        elif isinstance(node, Text):
            result.append(node.text())
        else:
            if node.tag in BLOCKS:
                result.append(' ')
                stack.append(' ')
            stack.extend(reversed(node.children))
    return ' '.join(''.join(result).split())


def _nearest(element, tag):
    return next((node for node in element.ancestors() if node.tag == tag), None)


def _grid(rows):
    if len(rows) > 5000:
        raise RemoteError('invalid_table', 'The table exceeds safe validation bounds')
    grid = [{} for _ in rows]
    for row_index, row in enumerate(rows):
        column = 0
        for cell in (n for n in row.children if isinstance(n, Element) and n.tag in ('td', 'th')):
            while column in grid[row_index]:
                column += 1
            try:
                width, height = (int(cell.attrs.get(key, '1')) for key in ('colspan', 'rowspan'))
            except (ValueError, TypeError):
                raise RemoteError('invalid_table', 'Ambiguous native table span') from None
            if not (1 <= width <= 100 and 1 <= height <= 5000 and column + width <= 100 and row_index + height <= len(rows)):
                raise RemoteError('invalid_table', 'Invalid native table span')
            for r in range(row_index, row_index + height):
                for c in range(column, column + width):
                    if c in grid[r]:
                        raise RemoteError('invalid_table', 'Overlapping native table spans')
                    grid[r][c] = cell
            column += width
    return grid


def reject_secrets(body, token=''):
    document = parse_storage(body)
    elements = list(document.descendants())
    visible = _visible(document)
    found = (bool(token) and (token in body or token in visible)) or any(
        pattern.search(body) or pattern.search(visible) for pattern in PATTERNS)
    locations = []
    for table_index, table in enumerate((n for n in elements if n.tag == 'table'), 1):
        rows = [n for n in table.descendants() if n.tag == 'tr' and _nearest(n, 'table') is table]
        try:
            grid = _grid(rows)
        except RemoteError:
            if not found:
                raise
            break
        for r, row in enumerate(rows):
            cells = [n for n in row.children if isinstance(n, Element) and n.tag in ('td', 'th')]
            for c, label in enumerate(cells):
                field = FIELDS.get(re.sub(r'[_ -]+', ' ', _visible(label).strip().lower()))
                if field is None:
                    continue
                if c + 1 < len(cells) and cells[c + 1].tag == 'td':
                    values = [cells[c + 1]]
                else:
                    columns = [key for key, value in grid[r].items() if value is label]
                    values = list(dict.fromkeys(grid[later][key] for later in range(r + 1, len(rows))
                                                for key in columns if key in grid[later] and grid[later][key] is not label))
                for value in values:
                    text = _visible(value).strip()
                    if not text or text in ('REDACTED', '환경 변수', '없음', '***'):
                        continue
                    if value.tag == 'th' and re.fullmatch(r'(?i)(?:dev(?:elopment)?|개발|qa)(?:\s*[1-9][0-9]*차)?', text):
                        continue
                    found = True
                    if len(locations) < 10:
                        locations.append(dict(block='table', index=table_index, row=r + 1, field=field))
    if found:
        raise RemoteError('secret_detected', 'Content resembles private authentication material; redact it before writing',
            details={'locations': locations or [dict(block='document', index=1, field='secret')]},
            next='Replace private values with REDACTED or an environment reference. Keep credentials outside the Wiki.')
