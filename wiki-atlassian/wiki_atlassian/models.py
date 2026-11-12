"""Native values and bounded, non-secret failures shared by the wiki layers."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re


def compact_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(compact_json(value).encode('utf-8')).hexdigest()


class RemoteError(Exception):
    def __init__(self, code, message='The native operation could not be completed',
                 outcome_unknown=False, details=None, next=None):
        super().__init__(message)
        self.code = code if isinstance(code, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,63}', code) else 'remote_error'
        self.outcome_unknown = bool(outcome_unknown)
        self.details = {} if details is None else details
        self.next = next


@dataclass(frozen=True)
class Credentials:
    base_url: str
    email: str
    api_token: str = field(repr=False)


@dataclass(frozen=True)
class Node:
    id: str
    title: str
    parent_id: str | None = None
    type: str = 'page'
    space_id: str = ''
    version: int = 0
    status: str = 'current'


@dataclass(frozen=True)
class Page:
    node: Node
    body: str
    version_message: str = ''


def native_id(value):
    if not isinstance(value, str) or re.fullmatch(r'[1-9][0-9]{0,31}', value) is None:
        raise RemoteError('invalid_request', 'Use an exact positive native ID')
    return value
