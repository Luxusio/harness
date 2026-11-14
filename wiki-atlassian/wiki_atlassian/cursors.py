"""Bounded, scope-bound navigation locators; never authorization."""
import base64
import hashlib
import hmac
import json

from .models import RemoteError, compact_json, digest


def encode_cursor(binding, state, *, key=None):
    raw = compact_json({'binding': digest(binding), 'state': state}).encode('utf-8')
    result = base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')
    if key is not None:
        result += '.' + hmac.new(key, raw, hashlib.sha256).hexdigest()
    if len(result) > 32768:
        raise RemoteError('invalid_cursor', 'Continuation exceeds the supported bound')
    return result


def decode_cursor(value, binding, *, key=None):
    try:
        if not isinstance(value, str) or not 1 <= len(value) <= 32768:
            raise ValueError()
        if key is not None:
            value, signature = value.split('.')
            if len(signature) != 64 or any(c not in '0123456789abcdef' for c in signature):
                raise ValueError()
        raw = base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
        if key is not None and not hmac.compare_digest(signature, hmac.new(key, raw, hashlib.sha256).hexdigest()):
            raise ValueError()
        data = json.loads(raw.decode('utf-8'))
        if type(data) is not dict or set(data) != {'binding', 'state'} or data['binding'] != digest(binding) or type(data['state']) is not dict:
            raise ValueError()
        return data['state']
    except (ValueError, TypeError, UnicodeError):
        raise RemoteError('invalid_cursor', 'Continuation changed or belongs to another selection; read again') from None
