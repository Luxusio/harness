"""Pure native document reference validation, shared by transport and edits."""
import re
from urllib.parse import unquote, urlsplit

from .models import RemoteError, native_id


def native_page_url_id(value, origin, space=None, allow_api=False):
    try:
        if (not isinstance(value, str) or not value.startswith('https://') or len(value) > 4096
                or any(c.isspace() or ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)
                or '\\' in value or '?' in value or '#' in value):
            raise ValueError()
        uri, expected = urlsplit(value), urlsplit(origin)
        if any(character in uri.path for character in '[]<>"{}|^`'):
            raise ValueError()
        if (expected.scheme != 'https' or not expected.hostname or expected.username is not None
                or expected.password is not None or expected.path not in ('', '/') or expected.query or expected.fragment
                or (uri.scheme, uri.hostname, uri.port) != (expected.scheme, expected.hostname, expected.port)
                or uri.username is not None or uri.password is not None or uri.port == 0):
            raise ValueError()
        page = re.fullmatch(r'/wiki/spaces/([A-Za-z0-9_-]+)/pages/([1-9][0-9]*)(?:/([^/]*))?', uri.path)
        api = re.fullmatch(r'/wiki/api/v2/pages/([1-9][0-9]*)(?:/([^/]*))?', uri.path) if allow_api else None
        if page is None and api is None:
            raise ValueError()
        if page is not None:
            if space is not None and page[1] != space:
                raise RemoteError('space_mismatch', 'Page URL space differs from its admitted namespace')
            selected, slug = page[2], page[3] or ''
        else:
            selected, slug = api[1], api[2] or ''
        if re.search(r'%(?![0-9a-fA-F]{2})', slug):
            raise ValueError()
        decoded = unquote(slug, errors='strict')
        if (decoded in ('.', '..') or any(c in '/\\' or ord(c) < 32 or 127 <= ord(c) <= 159 for c in decoded)
                or re.search(r'%2e|%2f|%5c', decoded, re.I)):
            raise ValueError()
        return native_id(selected)
    except (TypeError, ValueError, UnicodeError):
        raise RemoteError('invalid_request', 'Use an exact page URL on the configured origin') from None
