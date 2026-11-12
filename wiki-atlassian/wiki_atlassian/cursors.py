"""Bounded, scope-bound navigation locators; never authorization."""
import base64
import json

from .models import RemoteError, compact_json, digest


def encode_cursor(binding, state):
    raw = compact_json({'binding': digest(binding), 'state': state}).encode('utf-8')
    result = base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')
    if len(result) > 32768:
        raise RemoteError('invalid_cursor', 'Continuation exceeds the supported bound')
    return result


def decode_cursor(value, binding):
    try:
        if not isinstance(value, str) or not 1 <= len(value) <= 32768:
            raise ValueError()
        raw = base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
        data = json.loads(raw.decode('utf-8'))
        if type(data) is not dict or set(data) != {'binding', 'state'} or data['binding'] != digest(binding) or type(data['state']) is not dict:
            raise ValueError()
        return data['state']
    except (ValueError, TypeError, UnicodeError):
        raise RemoteError('invalid_cursor', 'Continuation changed or belongs to another selection; read again') from None
