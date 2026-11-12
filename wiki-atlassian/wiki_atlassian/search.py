"""Lexical ranking on authored projections with original Unicode offsets."""
from __future__ import annotations
from dataclasses import dataclass
import re
import unicodedata


@dataclass(frozen=True)
class Match:
    rank: int
    field: str
    body_offset: int = -1


def _pattern(value):
    source = re.escape(value)
    if re.fullmatch('[0-9]+', value):
        source = r'(?<![0-9])' + source + r'(?![0-9])'
    return re.compile(source, re.IGNORECASE)


def original_offset(content, normalized, offset):
    if offset <= 0 or content == normalized:
        return offset
    # NFC only composes within canonical combining sequences, plus Hangul Jamo.
    # Locate the normalization prefix boundary without quadratic prefix scans.
    start, position = 0, 0
    for end in range(1, len(content) + 1):
        if end < len(content):
            previous, following = ord(content[end - 1]), ord(content[end])
            # A trailing consonant and the next syllable's initial consonant
            # are separate clusters, even though both are Hangul Jamo.
            hangul = ((0x1100 <= previous <= 0x1112 and 0x1161 <= following <= 0x1175)
                      or ((0x1161 <= previous <= 0x1175
                           or (0xAC00 <= previous <= 0xD7A3 and (previous-0xAC00) % 28 == 0))
                          and 0x11A8 <= following <= 0x11C2))
            if unicodedata.combining(content[end]) or hangul:
                continue
        position += len(unicodedata.normalize('NFC', content[start:end]))
        if position > offset:
            return start
        start = end
    raise ValueError('Normalized match outside projection')


class Query:
    def __init__(self, query):
        words = unicodedata.normalize('NFC', query).split()
        if not words:
            raise ValueError('Query must contain searchable text')
        self.phrase = _pattern(' '.join(words))
        self.terms = [_pattern(word) for word in dict.fromkeys(words)]

    def match(self, title, content):
        title_n, body = unicodedata.normalize('NFC', title), unicodedata.normalize('NFC', content)
        title_terms = [bool(p.search(title_n)) for p in self.terms]
        phrase_body = self.phrase.search(body)
        starts = [m.start() if (m := p.search(body)) else -1 for p in self.terms]
        rank = 6 if self.phrase.fullmatch(title_n) else 5 if self.phrase.search(title_n) else 4 if all(title_terms) else 0
        if rank:
            at = phrase_body.start() if phrase_body else next((i for i in starts if i >= 0), -1)
            return Match(rank, 'title', original_offset(content, body, at))
        if phrase_body:
            return Match(3, 'body', original_offset(content, body, phrase_body.start()))
        if any(at < 0 and not title_terms[i] for i, at in enumerate(starts)):
            return None
        all_body = all(at >= 0 for at in starts)
        at = min(at for i, at in enumerate(starts) if at >= 0 and (all_body or not title_terms[i]))
        return Match(2 if all_body else 1, 'body' if all_body else 'title_and_body', original_offset(content, body, at))


_HEADER = re.compile(r'\A---\npage_id: "[1-9][0-9]*"\nversion: [0-9]+\nurl: [^\n]+\n---\n\n')


def authored_start(markdown):
    match = _HEADER.match(markdown)
    return match.end() if match else 0


def projected_headings(content, start, at):
    outline, path, fenced = [], [], False
    offset = start
    for line in content[start:].splitlines(keepends=True):
        if offset > at and not path:
            return None
        if line.startswith('```'):
            fenced = not fenced
        heading = None if fenced else re.match(r'^(#{1,6}) (.*?)(?:\n)?$', line)
        if heading:
            level, title = len(heading[1]), heading[2].strip()
            if len(outline) >= 128 or len(title) > 120:
                return None
            outline.append((level, title))
            if offset <= at:
                while path and path[-1][0] >= level:
                    path.pop()
                path.append((level, title))
        offset += len(line)
    return (outline, [title for _, title in path]) if not fenced and path and all(t for _, t in path) else None
