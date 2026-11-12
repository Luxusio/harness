"""Bounded native Confluence HTTP, current admission and single-attempt writes.

Remote storage is never parsed and reserialized here. Authorization observations
belong to one operation and are revalidated before their content is returned.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from datetime import datetime
import hashlib
import http.client
import json
import re
import ssl
import time
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit

from .models import Credentials, Node, Page, RemoteError, compact_json, native_id

MAX_BODY = 8 * 1024 * 1024
SCOPE_CODES = {'http_401', 'http_403', 'http_404', 'scope_not_current', 'scope_outside_connection'}


def is_scope_denial(error):
    return isinstance(error, RemoteError) and error.code in SCOPE_CODES


class RootScopeDenial(RemoteError):
    def __init__(self, settings, failure):
        super().__init__(failure.code, str(failure), failure.outcome_unknown, failure.details, failure.next)
        self.settings = settings
        self.__cause__ = failure


def _need(condition, code='malformed_node', message='Native metadata is incomplete or invalid'):
    if not condition:
        raise RemoteError(code, message)


def _text(value, maximum=4096, optional=False):
    if value is None and optional:
        return None
    _need(isinstance(value, str) and bool(value.strip()) and len(value) <= maximum and
          not any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value))
    return value


def _positive(value):
    _need(type(value) is int and 0 < value <= 2**31 - 1)
    return value


def _origin(value):
    try:
        uri = urlsplit(value)
        _need(isinstance(value, str) and uri.scheme == 'https' and uri.hostname and
              uri.username is None and uri.password is None and uri.path in ('', '/') and
              not uri.query and not uri.fragment and uri.port != 0 and
              not any(c.isspace() or ord(c) < 32 or c == '\\' for c in value),
              'invalid_origin', 'Configured Atlassian URL must be an HTTPS origin')
        return value.rstrip('/')
    except (ValueError, TypeError):
        raise RemoteError('invalid_origin', 'Configured Atlassian URL must be an HTTPS origin') from None


def native_page_url_id(value, origin, space=None, allow_api=False):
    _need(isinstance(value, str) and len(value) <= 4096, 'invalid_request', 'Bounded native page URL required')
    try:
        uri, expected = urlsplit(value), urlsplit(_origin(origin))
        _need((uri.scheme, uri.hostname, uri.port) == (expected.scheme, expected.hostname, expected.port) and
              uri.username is None and uri.password is None and not uri.query and not uri.fragment and
              '\\' not in uri.path and not any(p in ('.', '..') for p in uri.path.split('/')),
              'invalid_request', 'Use an exact page URL on the configured origin')
        page = re.fullmatch(r'/wiki/spaces/([A-Za-z0-9_-]+)/pages/([1-9][0-9]*)(?:/([^/]*))?', uri.path)
        api = re.fullmatch(r'/wiki/api/v2/pages/([1-9][0-9]*)(?:/([^/]*))?', uri.path) if allow_api else None
        _need(page is not None or api is not None, 'invalid_request', 'Use a literal native page ID or exact page URL')
        if page:
            _need(space is None or page[1] == space, 'space_mismatch', 'Page URL space differs from its admitted namespace')
            selected, slug = page[2], page[3] or ''
        else:
            selected, slug = api[1], api[2] or ''
        decoded = unquote(slug, errors='strict')
        _need(decoded not in ('.', '..') and not any(c in '/\\' or ord(c) < 32 or ord(c) == 127 for c in decoded) and
              re.search(r'%2e|%2f|%5c', decoded, re.I) is None,
              'invalid_request', 'Page title slug contains traversal or path separators')
        return native_id(selected)
    except (ValueError, UnicodeError):
        raise RemoteError('invalid_request', 'Use an exact page URL on the configured origin') from None


class _Budget:
    def __init__(self, requests, size, timeout, parent=None):
        self.maximum, self.size, self.deadline = requests, size, time.monotonic() + timeout
        self.requests = self.bytes = 0
        self.parent = parent
        if parent is not None:
            self.deadline = min(self.deadline, parent.deadline)

    def request(self):
        if self.parent is not None:
            self.parent.request()
        self.requests += 1
        _need(self.requests <= self.maximum and time.monotonic() < self.deadline,
              'wiki_request_limit', 'Operation exceeded its request/time budget; narrow the target')

    def accept(self, size, mutating):
        if self.parent is not None:
            self.parent.accept(size, mutating)
        self.bytes += size
        if self.bytes > self.size:
            raise RemoteError('wiki_body_limit', 'Operation exceeded its response body budget', mutating)


_budget = ContextVar('wiki_remote_budget', default=None)


class Remote:
    def __init__(self, credentials: Credentials, transport=None, *, ssl_context=None):
        self.credentials = credentials
        self.origin = _origin(credentials.base_url)
        self._transport = transport
        self._ssl_context = ssl_context or ssl.create_default_context()

    @contextmanager
    def operation(self, max_requests=512, max_response_bytes=32 * 1024 * 1024, timeout=45):
        _need(type(max_requests) is int and max_requests > 0 and type(max_response_bytes) is int and max_response_bytes > 0 and timeout > 0,
              'invalid_request', 'Positive operation budget required')
        prior = _budget.get()
        token = _budget.set(_Budget(max_requests, max_response_bytes, timeout, prior))
        try:
            yield
        finally:
            _budget.reset(token)

    def budget_remaining(self):
        budget = _budget.get()
        return budget is None or budget.requests < budget.maximum and budget.bytes < budget.size and time.monotonic() < budget.deadline

    def _url(self, value, base=None):
        _need(isinstance(value, str) and len(value) <= 16384 and not any(ord(c) < 32 or c == '\\' for c in value),
              'invalid_next', 'Invalid native API URL')
        if value.startswith('/api/v2/') or value.startswith('/rest/api/'):
            value = '/wiki' + value
        candidate = urljoin(base or self.origin, value)
        try:
            uri, expected = urlsplit(candidate), urlsplit(self.origin)
            _need((uri.scheme, uri.hostname, uri.port) == (expected.scheme, expected.hostname, expected.port) and
                  uri.username is None and uri.password is None and not uri.fragment and
                  uri.path.startswith(('/wiki/api/v2/', '/wiki/rest/api/')) and '%' not in uri.path and
                  not any(p in ('.', '..') for p in uri.path.split('/')),
                  'invalid_next', 'Native request must remain inside the configured API origin')
        except ValueError:
            raise RemoteError('invalid_next', 'Invalid native API URL') from None
        return candidate

    def _endpoint(self, path, params=None):
        return self._url(path) + ('?' + urlencode(params) if params else '')

    def _request(self, method, target, payload=None):
        target = self._url(target)
        budget = _budget.get()
        if budget:
            budget.request()
        timeout = min(60, max(.001, budget.deadline - time.monotonic())) if budget else 60
        mutating = method != 'GET'
        auth = base64.b64encode((self.credentials.email + ':' + self.credentials.api_token).encode()).decode()
        headers = {'Authorization': 'Basic ' + auth, 'Accept': 'application/json'}
        data = None if payload is None else compact_json(payload).encode('utf-8')
        if data is not None:
            headers['Content-Type'] = 'application/json'
        try:
            if self._transport is not None:
                status, response_headers, raw = self._transport(method, target, headers, data, timeout)
            else:
                status, response_headers, raw = self._https(method, target, headers, data, timeout)
        except Exception as error:
            if isinstance(error, RemoteError):
                raise
            raise RemoteError('transport_failed', 'Native request failed; inspect uncertain mutations before retrying', mutating) from None
        if type(status) is not int or not isinstance(response_headers, dict) or not isinstance(raw, bytes):
            raise RemoteError('malformed_response', 'Invalid transport response', mutating)
        if budget:
            budget.accept(len(raw), mutating)
        if not 200 <= status < 300:
            raise RemoteError('http_' + str(status), 'Confluence request failed (HTTP ' + str(status) + ')',
                              mutating and status >= 500, self._error_details(raw),
                              'Inspect native content and history using read-only tools; do not automatically repeat the write.' if mutating and status >= 500 else None)
        if len(raw) > MAX_BODY:
            raise RemoteError('response_limit', 'Confluence response exceeds the size limit', mutating)
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('duplicate')
                result[key] = value
            return result
        try:
            result = json.loads(raw.decode('utf-8'), object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('constant')))
            if not isinstance(result, dict):
                raise ValueError('object')
            # JSON escapes can smuggle isolated UTF-16 surrogates through an
            # otherwise valid UTF-8 envelope; those cannot enter native strings.
            compact_json(result).encode('utf-8')
        except (ValueError, UnicodeError, RecursionError):
            raise RemoteError('malformed_response', 'Confluence returned invalid JSON', mutating) from None
        return {str(k).lower(): v for k, v in response_headers.items()}, result

    def _https(self, method, target, headers, data, timeout):
        """Public http.client sockets allow a real deadline during slow bodies.

        A finite socket timeout alone resets after every successful read and can
        otherwise let a slow stream retain the worker indefinitely. Connections
        are owned by this request and closed on every exit, including redirects.
        """
        uri = urlsplit(target)
        deadline = time.monotonic() + timeout
        connection = http.client.HTTPSConnection(uri.hostname, uri.port, context=self._ssl_context, timeout=min(15, timeout))
        try:
            connection.connect()
            sock = connection.sock
            def remaining():
                value = deadline - time.monotonic()
                if value <= 0:
                    raise TimeoutError('native operation deadline')
                sock.settimeout(value)
            remaining()
            connection.request(method, uri.path + ('?' + uri.query if uri.query else ''), data, headers)
            remaining()
            response = connection.getresponse()
            limit = MAX_BODY if 200 <= response.status < 300 else 65536
            chunks, size = [], 0
            try:
                while size <= limit:
                    remaining()
                    chunk = response.read1(min(65536, limit + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if response.isclosed():
                        break
            except (OSError, http.client.HTTPException):
                if 200 <= response.status < 300:
                    raise
                # A native rejection is already definite even if its optional
                # diagnostic body is unreadable. Never turn a 409 into unknown.
                chunks = []
            response_headers = {}
            for key, value in response.getheaders():
                key = key.lower()
                response_headers[key] = response_headers[key] + ',' + value if key in response_headers else value
            return response.status, response_headers, b''.join(chunks)
        finally:
            connection.close()

    def _error_details(self, raw):
        if len(raw) > 65536:
            return {}
        try:
            value = json.loads(raw)
            entries = [value] + (value.get('errors', [])[:8] if isinstance(value.get('errors'), list) else [])
            allowed = {'INVALID_STORAGE', 'BAD_REQUEST', 'FORBIDDEN', 'VERSION_CONFLICT', 'CONFLICT', 'PERMISSION_DENIED', 'UNAUTHORIZED', 'NOT_FOUND', 'DUPLICATE_TITLE', 'TITLE_ALREADY_EXISTS', 'duplicateTitle', 'INVALID_PARENT', 'INVALID_SPACE'}
            secrets = (self.credentials.email, self.credentials.api_token,
                       base64.b64encode((self.credentials.email + ':' + self.credentials.api_token).encode()).decode())
            codes = list(dict.fromkeys(e[k] for e in entries if isinstance(e, dict) for k in ('code', 'errorCode')
                                      if isinstance(e.get(k), str) and e[k] in allowed and
                                      not any(secret and secret.casefold() in e[k].casefold() for secret in secrets)))[:3]
            messages = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                messages.extend(entry[k] for k in ('message', 'detail', 'title') if isinstance(entry.get(k), str))
                if isinstance(entry.get('errorMessages'), list):
                    messages.extend(x for x in entry['errorMessages'][:8] if isinstance(x, str))
            reasons = []
            for original in messages + codes:
                message = original[:2048]
                for secret in secrets:
                    if secret:
                        message = re.sub(re.escape(secret), '[redacted]', message, flags=re.I)
                message = re.sub(r'(?i)(?:basic|bearer)\s+[^\s,;]+|(?:authorization|password|token|secret|api[_-]?key)\s*[:=]\s*[^\s,;]+', '[redacted]', message).lower()
                reason = None
                if 'title' in message and any(x in message for x in ('duplicate', 'already exists', 'already_exists', 'alreadyexists', 'same title')):
                    reason = 'A page with this title already exists in the native space.'
                elif any(x in message for x in ('invalid parent', 'invalid_parent', 'parent is invalid', 'parent not found', 'parent does not exist', 'parent id is invalid')):
                    reason = 'The native parent is invalid or unavailable.'
                elif any(x in message for x in ('invalid space', 'invalid_space', 'space is invalid', 'space not found', 'space does not exist', 'space id is invalid')):
                    reason = 'The native space is invalid or unavailable.'
                elif ('storage' in message or 'xhtml' in message) and any(x in message for x in ('invalid', 'mismatch', 'parse', 'malformed')):
                    reason = 'Native storage XHTML is invalid or malformed.'
                elif any(x in message for x in ('permission', 'forbidden', 'not authorized', 'access denied')):
                    reason = 'The native account lacks permission for this operation.'
                elif 'version' in message and any(x in message for x in ('conflict', 'current', 'latest', 'mismatch')):
                    reason = 'The requested native version conflicts with the current version.'
                if reason and reason not in reasons:
                    reasons.append(reason)
            result = {}
            if codes:
                result['providerCodes'] = codes
            if reasons:
                result['providerMessages'] = reasons[:3]
            return result
        except (ValueError, AttributeError, TypeError, RecursionError):
            return {}

    @staticmethod
    def _node_mismatch(actual, expected):
        fields = {'id': 'id', 'title': 'title', 'parent_id': 'parentId', 'type': 'type',
                  'space_id': 'spaceId', 'version': 'version', 'status': 'status'}
        return [public for name, public in fields.items() if getattr(actual, name) != getattr(expected, name)]

    def _parse_node(self, obj, expected_type='page', expected_id=None, current=True):
        _need(isinstance(obj, dict))
        selected = native_id(obj.get('id'))
        _need(expected_id is None or selected == expected_id, 'identity_mismatch', 'Confluence returned another native identity')
        _need(obj.get('type', expected_type) == expected_type and expected_type in ('page', 'folder'))
        title = _text(obj.get('title'), 1000)
        direct = obj.get('spaceId')
        nested = obj.get('space')
        nested_id = nested.get('id') if isinstance(nested, dict) else None
        _need(direct is None or isinstance(direct, str))
        if type(nested_id) is int:
            _need(0 < nested_id <= 2**63 - 1)
            nested_id = str(nested_id)
        space_id = native_id(direct if direct is not None else nested_id)
        _need(int(space_id) <= 2**63 - 1 and (nested_id is None or native_id(nested_id) == space_id))
        status = obj.get('status')
        _need(isinstance(status, str) and bool(status.strip()))
        if current:
            _need(status == 'current', 'scope_not_current', 'Native content must be explicitly current')
        version = obj.get('version', {})
        _need(isinstance(version, dict))
        number = _positive(version.get('number')) if expected_type == 'page' else version.get('number', 0)
        _need(type(number) is int and number >= 0)
        parent = obj.get('parentId')
        ancestors = obj.get('ancestors')
        if ancestors is not None:
            _need(isinstance(ancestors, list) and len(ancestors) <= 100)
            ancestry = [native_id(row.get('id')) for row in ancestors if isinstance(row, dict)]
            _need(len(ancestry) == len(ancestors) and len(set(ancestry)) == len(ancestry) and selected not in ancestry)
            _need('parentId' not in obj or parent == (ancestry[-1] if ancestry else None))
            parent = ancestry[-1] if ancestry else None
        if parent is not None:
            native_id(parent)
        _need(parent != selected)
        return Node(selected, title, parent, expected_type, space_id, number, status)

    def node(self, id, type='page'):
        return self.scope_node(id, type)

    def scope_node(self, id, type='page'):
        native_id(id)
        _need(type in ('page', 'folder'), 'invalid_request', 'Only pages and folders are supported')
        return self._parse_node(self._request('GET', self._endpoint('/wiki/api/v2/' + ('folders' if type == 'folder' else 'pages') + '/' + id))[1], type, id)

    def _connection_node(self, scope, id, type='page'):
        _need(scope.site.rstrip('/') == self.origin, 'scope_outside_connection', 'Selected site differs from authenticated origin')
        try:
            node = self.scope_node(id, type)
            _need(node.space_id == scope.space_id, 'scope_outside_connection', 'Native content is outside the configured space')
            return node
        except RemoteError as failure:
            if is_scope_denial(failure) and (id == scope.root_page_id or scope.whole_space and failure.code == 'http_401'):
                raise RootScopeDenial(scope, failure) from failure
            raise

    def _any_node(self, scope, id):
        try:
            return self._connection_node(scope, id)
        except RemoteError as error:
            if error.code != 'http_404':
                raise
            return self._connection_node(scope, id, 'folder')

    def ancestry(self, settings, id):
        native_id(id)
        observed = []
        current = id
        while current is not None:
            _need(len(observed) < 100 and current not in {n.id for n in observed}, 'scope_limit', 'Cyclic or excessive native ancestry')
            node = self._any_node(settings, current)
            observed.append(node)
            if settings.whole_space or node.id == settings.root_page_id:
                break
            current = node.parent_id
        _need(settings.whole_space or observed[-1].id == settings.root_page_id,
              'scope_outside_connection', 'Document is outside the configured root')
        self._verify(settings, observed)
        return observed

    def _verify(self, scope, observed):
        for prior in observed:
            _need(self._connection_node(scope, prior.id, prior.type) == prior,
                  'scope_metadata_changed', 'Native ancestry changed before its observations could be returned')

    def require_scope(self, settings, id):
        return self.ancestry(settings, id)[0]

    def _move_chain(self, settings, id):
        if not settings.whole_space:
            return self.ancestry(settings, id)
        result, current = [], id
        while current is not None:
            _need(len(result) < 100 and current not in {n.id for n in result}, 'scope_limit', 'Cyclic or excessive move ancestry')
            node = self._any_node(settings, current)
            result.append(node)
            current = node.parent_id
        self._verify(settings, result)
        return result

    def require_space(self, settings):
        _need(settings.site.rstrip('/') == self.origin, 'scope_outside_connection', 'Selected site differs from authenticated origin')
        try:
            spaces = self.spaces(settings.space)
            _need(len(spaces) == 1 and spaces[0]['id'] == settings.space_id, 'scope_outside_connection', 'Configured space identity is no longer accessible')
        except RemoteError as failure:
            if settings.whole_space and is_scope_denial(failure):
                raise RootScopeDenial(settings, failure) from failure
            raise

    def page(self, id, expected_node=None, scope=None, fresh_storage=False):
        native_id(id)
        observed = self.ancestry(scope, id) if scope is not None else [self.scope_node(id)]
        node = observed[0]
        _need(node.type == 'page', 'scope_metadata_changed', 'Page differs from the admitted native identity')
        if expected_node is not None and node != expected_node:
            raise RemoteError('readback_mismatch', 'Page differs from its expected native identity',
                              details={'mismatchFields': self._node_mismatch(node, expected_node)})
        metadata = self._request('GET', self._endpoint('/wiki/api/v2/pages/' + id))[1]
        _need(self._parse_node(metadata, 'page', id) == node, 'scope_metadata_changed', 'Page changed before its storage read')
        message = metadata.get('version', {}).get('message', '')
        _need(isinstance(message, str))
        if scope:
            self._verify(scope, observed)
        raw = self._request('GET', self._endpoint('/wiki/api/v2/pages/' + id, {'body-format': 'storage'}))[1]
        result = self._parse_page(raw, id)
        _need(result.node == node and result.version_message == message, 'scope_metadata_changed', 'Page changed during storage read')
        if scope:
            self._verify(scope, observed)
        else:
            _need(self.scope_node(id) == node, 'scope_metadata_changed', 'Page changed during storage read')
        return result

    def _parse_page(self, raw, id, current=True):
        node = self._parse_node(raw, 'page', id, current)
        container = raw.get('body')
        _need(isinstance(container, dict), 'missing_storage', 'Native storage is unavailable')
        storage = container.get('storage', {})
        _need(isinstance(storage, dict) and isinstance(storage.get('value'), str) and storage.get('representation', 'storage') == 'storage',
              'missing_storage', 'Native storage is unavailable')
        body = storage['value']
        _need(len(body.encode()) <= 4 * 1024 * 1024, 'response_limit', 'Native storage exceeds its read budget')
        message = raw.get('version', {}).get('message', '')
        _need(isinstance(message, str))
        return Page(node, body, message)

    def scoped_page(self, settings, id, fresh_storage=False):
        return self.page(id, scope=settings, fresh_storage=fresh_storage)

    def _continuation(self, value, path, params):
        _need(isinstance(value, str) and 0 < len(value) <= 8192, 'invalid_next', 'Invalid native continuation')
        checked = self._url(path + value if value.startswith('?') else value, self._endpoint(path, params))
        parsed = urlsplit(checked)
        _need(parsed.path == path, 'invalid_next', 'Continuation changed its endpoint')
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        values = {}
        for key, item in pairs:
            _need(key not in values and key in set(params) | {'cursor', 'start', 'startAt'} and bool(item) and len(item) <= 4096,
                  'invalid_next', 'Continuation changed its query')
            values[key] = item
        _need(all(key not in values or values[key] == str(expected) for key, expected in params.items()), 'invalid_next', 'Continuation changed scope or limit')
        return self._endpoint(path, dict(params, **{k: v for k, v in values.items() if k not in params}))

    def _next(self, headers, raw, path, params):
        links = raw.get('_links', {})
        _need(isinstance(links, dict), 'invalid_next', 'Invalid native continuation metadata')
        body = self._continuation(links['next'], path, params) if 'next' in links else None
        header = headers.get('link')
        found = []
        if header is not None:
            _need(isinstance(header, str), 'invalid_next', 'Invalid native continuation header')
            for part in header.split(','):
                if re.search(r'\brel\s*=\s*"?next"?(?:\s|;|$)', part):
                    match = re.search(r'<([^>]+)>', part)
                    _need(match is not None, 'invalid_next', 'Invalid native continuation header')
                    found.append(self._continuation(match[1], path, params))
        _need(len(found) <= 1 and (body is None or not found or body == found[0]), 'invalid_next', 'Native continuations disagree')
        return body or (found[0] if found else None)

    def batch(self, path, parameters, cursor=None):
        params = {k: str(v) for k, v in parameters.items()}
        target, seen = self._endpoint(path, params), []
        if cursor is not None:
            try:
                _need(isinstance(cursor, str) and len(cursor) <= 16000, 'invalid_next', 'Invalid native continuation')
                state = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
                _need(isinstance(state, dict) and set(state) == {'next', 'seen'} and isinstance(state['seen'], list), 'invalid_next', 'Invalid native continuation')
                seen = state['seen']
                _need(len(seen) <= 32 and all(isinstance(x, str) and re.fullmatch('[a-f0-9]{64}', x) for x in seen) and len(set(seen)) == len(seen), 'invalid_next', 'Invalid continuation history')
                target = self._continuation(state['next'], path, params)
            except (ValueError, TypeError, UnicodeError):
                raise RemoteError('invalid_next', 'Invalid native continuation') from None
        identity = hashlib.sha256(target.encode()).hexdigest()
        _need(identity not in seen, 'pagination_loop', 'Native continuation is cyclic')
        seen = seen + [identity]
        headers, raw = self._request('GET', target)
        rows = raw.get('results')
        _need(isinstance(rows, list) and all(isinstance(x, dict) for x in rows) and len(rows) <= int(params.get('limit', 250)), 'malformed_list', 'Native list is incomplete or exceeds its bound')
        next_url = self._next(headers, raw, path, params)
        _need(next_url is None or len(seen) < 32 and hashlib.sha256(next_url.encode()).hexdigest() not in seen, 'pagination_loop', 'Native continuation is cyclic or excessive')
        result = {'results': rows, 'hasMore': next_url is not None, 'complete': next_url is None}
        if next_url:
            result['cursor'] = base64.urlsafe_b64encode(compact_json({'next': next_url, 'seen': seen}).encode()).decode().rstrip('=')
        return result

    def _list(self, path, params):
        result, cursor = [], None
        while True:
            batch = self.batch(path, params, cursor)
            result.extend(batch['results'])
            _need(len(result) <= 20000, 'inventory_limit', 'Native inventory exceeds its node budget')
            cursor = batch.get('cursor')
            if cursor is None:
                return result

    def spaces(self, key=None):
        _need(key is None or isinstance(key, str) and re.fullmatch('[A-Za-z0-9_-]{1,100}', key), 'invalid_request', 'Invalid configured space key')
        params = {'limit': 250, 'status': 'current'}
        if key is not None:
            params['keys'] = key
        rows = self._list('/wiki/api/v2/spaces', params)
        result = []
        for raw in rows:
            if key is not None and raw.get('key') != key:
                continue
            native = native_id(raw.get('id'))
            actual_key = raw.get('key')
            _need(isinstance(actual_key, str) and re.fullmatch('[A-Za-z0-9_-]{1,100}', actual_key) and raw.get('status', 'current') == 'current', 'malformed_list', 'Invalid native space identity')
            row = {'id': native, 'key': actual_key, 'name': raw.get('name', actual_key)}
            home = raw.get('homepageId', raw.get('homePageId'))
            if home is not None:
                row['homePageId'] = native_id(home)
            result.append(row)
        return result

    def current_user(self):
        raw = self._request('GET', self._endpoint('/wiki/rest/api/user/current'))[1]
        _need(raw.get('type', 'known') == 'known', 'account_unavailable', 'Authenticated Confluence account is unavailable')
        return {'accountId': _text(raw.get('accountId'), 500), 'displayName': _text(raw.get('displayName'), 500)}

    def inventory(self, settings):
        with self.operation():
            if settings.whole_space:
                self.require_space(settings)
                rows = self._list('/wiki/api/v2/spaces/' + native_id(settings.space_id) + '/pages', {'limit': 250, 'status': 'current'})
                pages = [self._parse_node(raw, 'page') for raw in rows]
                folders = self._list('/wiki/rest/api/content/search', {'cql': 'space = "' + settings.space + '" and type = folder', 'expand': 'space,version,ancestors', 'limit': 250})
                nodes = pages + [self._parse_node(raw, 'folder') for raw in folders]
            else:
                root = self.require_scope(settings, settings.root_page_id)
                nodes, pending = [root], [(root, 0)]
                while pending:
                    parent, depth = pending.pop(0)
                    _need(depth <= 100 and self.require_scope(settings, parent.id) == parent, 'scope_metadata_changed', 'Native hierarchy changed during inventory')
                    path = '/wiki/api/v2/' + ('folders' if parent.type == 'folder' else 'pages') + '/' + parent.id + '/direct-children'
                    for raw in self._list(path, {'limit': 250}):
                        _need(isinstance(raw.get('type'), str), 'malformed_child', 'Child content type is unavailable')
                        if raw['type'] not in ('page', 'folder'):
                            continue
                        child = self._connection_node(settings, native_id(raw.get('id')), raw['type'])
                        _need(child.parent_id == parent.id and child.id not in {n.id for n in nodes} and len(nodes) < 20000,
                              'scope_metadata_changed', 'Duplicate, excessive or changed child hierarchy')
                        nodes.append(child)
                        pending.append((child, depth + 1))
            _need(len(nodes) <= 20000 and len({n.id for n in nodes}) == len(nodes) and all(n.space_id == settings.space_id for n in nodes), 'malformed_list', 'Native inventory identities are invalid')
            mapping = {n.id: n for n in nodes}
            for node in nodes:
                current, seen = node, set()
                while current is not None:
                    _need(current.id not in seen and len(seen) < 100, 'scope_limit', 'Cyclic or excessive inventory ancestry')
                    seen.add(current.id)
                    if not settings.whole_space and current.id == settings.root_page_id:
                        break
                    current = mapping.get(current.parent_id)
                _need(settings.whole_space or settings.root_page_id in seen, 'scope_outside_connection', 'Inventory left its configured root')
            self._verify(settings, reversed(nodes))
            if settings.whole_space:
                self.require_space(settings)
            else:
                _need(self.require_scope(settings, settings.root_page_id) == nodes[0], 'scope_metadata_changed', 'Native root changed during inventory')
            return nodes

    def version(self, page_id, number):
        native_id(page_id)
        _positive(number)
        raw = self._request('GET', self._endpoint('/wiki/api/v2/pages/' + page_id + '/versions/' + str(number)))[1]
        _need(type(raw.get('number')) is int and raw['number'] == number, 'readback_mismatch', 'Exact native version metadata is unavailable')
        _text(raw.get('authorId'), 500)
        _text(raw.get('createdAt'), 100)
        _need('message' not in raw or isinstance(raw['message'], str))
        return raw

    def require_page_version(self, page):
        return self.version(page.node.id, page.node.version)

    def list_versions(self, page_id, limit=20, cursor=None, scope=None):
        _need(type(limit) is int and 1 <= limit <= 50, 'invalid_request', 'Version limit must be 1..50')
        observed = self.ancestry(scope, page_id) if scope else [self.scope_node(page_id)]
        result = self.batch('/wiki/api/v2/pages/' + native_id(page_id) + '/versions', {'limit': limit}, cursor)
        numbers = []
        for item in result['results']:
            number = _positive(item.get('number'))
            _need(number <= observed[0].version)
            _text(item.get('authorId'), 500)
            _text(item.get('createdAt'), 100)
            numbers.append(number)
        _need(len(set(numbers)) == len(numbers), 'malformed_list', 'Native history contains duplicate versions')
        if scope:
            self._verify(scope, observed)
        return result

    def historical_page(self, page_id, version, scope):
        observed = self.ancestry(scope, page_id)
        _need(type(version) is int and 1 <= version <= observed[0].version, 'invalid_request', 'Invalid native historical version')
        raw = self._request('GET', self._endpoint('/wiki/api/v2/pages/' + page_id, {'body-format': 'storage', 'version': version}))[1]
        result = self._parse_page(raw, page_id, current=False)
        _need(result.node.version == version and result.node.space_id == observed[0].space_id and result.node.status in ('current', 'historical'), 'identity_mismatch', 'Historical page identity differs')
        self._verify(scope, observed)
        return result

    def ensure_no_draft(self, id, expected_node=None, scope=None):
        from .storage import equivalent
        before = self.page(id, expected_node, scope, True)
        path = '/wiki/api/v2/pages/' + native_id(id)
        try:
            metadata = self._request('GET', self._endpoint(path, {'get-draft': 'true'}))[1]
            node = self._parse_node(metadata, 'page', id, current=False)
            _need(node.status in ('current', 'draft') and 1 <= node.version <= before.node.version and
                  replace(node, status='current', version=before.node.version) == before.node,
                  'draft_unverified', 'Draft identity differs from the published page')
            if scope:
                _need(self.require_scope(scope, id) == before.node, 'scope_metadata_changed', 'Scope changed while checking draft')
            draft = self._parse_page(self._request('GET', self._endpoint(path, {'body-format': 'storage', 'get-draft': 'true'}))[1], id, current=False)
            _need(draft.node == node and draft.version_message == metadata.get('version', {}).get('message', ''), 'draft_unverified', 'Draft changed during its storage read')
        except RemoteError as error:
            if error.code == 'http_404':
                raise RemoteError('draft_unverified', 'Draft absence could not be verified; automatic update is blocked') from error
            raise
        _need(equivalent(draft.body, before.body) and
              ((draft.node.status == 'current' and draft.node == before.node and draft.version_message == before.version_message) or
               (draft.node.status == 'draft' and draft.version_message in ('', before.version_message))),
              'draft_unverified', 'Unpublished or divergent draft prevents automatic update')
        if scope:
            _need(self.require_scope(scope, id) == before.node, 'scope_metadata_changed', 'Scope changed while checking draft')

    @staticmethod
    def _unknown(code, message, error=None, reference=None, mismatch=None):
        details = dict(error.details) if isinstance(error, RemoteError) else {}
        if reference is not None:
            details['reference'] = reference
        if mismatch:
            details['mismatchFields'] = mismatch
        failure = RemoteError(code, message, True, details,
                              'Inspect native content and history using read-only tools; do not automatically repeat the write.')
        if error is not None:
            failure.__cause__ = error
        return failure

    @staticmethod
    def _page_equivalent(actual, expected):
        from .storage import native_page_readback
        return native_page_readback(actual, expected)

    def create(self, space_id, parent_id, title, body, message, scope, before_effect=None):
        native_id(space_id)
        _text(title, 1000)
        _need(isinstance(body, str) and len(body.encode()) <= 4 * 1024 * 1024, 'invalid_request', 'Bounded native body required')
        _need(scope is not None and scope.space_id == space_id and (parent_id is not None or scope.whole_space), 'selection_required', 'An explicit parent or whole-space destination is required')
        parent = self.require_scope(scope, native_id(parent_id)) if parent_id is not None else None
        if parent is None:
            self.require_space(scope)
        account = self.current_user()
        if parent is None:
            self.require_space(scope)
            payload = {'spaceId': space_id, 'status': 'current', 'title': title, 'body': {'representation': 'storage', 'value': body}}
            path, params = '/wiki/api/v2/pages', {'root-level': 'true'}
        else:
            _text(message, 4096)
            spaces = self.spaces(scope.space)
            _need(len(spaces) == 1 and spaces[0]['id'] == space_id, 'space_unavailable', 'Configured space is not uniquely accessible')
            payload = {'type': 'page', 'status': 'current', 'title': title, 'space': {'key': scope.space}, 'ancestors': [{'id': parent_id}],
                       'body': {'storage': {'representation': 'storage', 'value': body}}, 'version': {'number': 1, 'message': message}}
            path, params = '/wiki/rest/api/content', None
        if parent:
            _need(self.require_scope(scope, parent_id) == parent, 'scope_metadata_changed', 'Creation parent changed')
        else:
            self.require_space(scope)
        _need(self.current_user() == account, 'account_unavailable', 'Authenticated account changed before saving')
        if before_effect:
            before_effect()
        raw = self._request('POST', self._endpoint(path, params), payload)[1]
        try:
            created = native_id(raw.get('id'))
        except RemoteError as error:
            raise self._unknown('unknown_create', 'Creation response has no valid page ID; reconcile before retrying', error) from error
        expected = Node(created, title, parent_id, 'page', space_id, 1)
        try:
            saved = self.page(created, expected, scope, True)
            revision = self.version(created, 1)
            _need(revision['authorId'] == account['accountId'] and self.current_user() == account,
                  'readback_mismatch', 'Created page account readback differs')
            datetime.fromisoformat(revision['createdAt'].replace('Z', '+00:00'))
            mismatch = []
            if saved.node != expected:
                mismatch.append('identity')
            if not self._page_equivalent(saved.body, body):
                mismatch.append('content')
            if parent is not None and saved.version_message != message:
                mismatch.append('message')
            if mismatch:
                raise RemoteError('readback_mismatch', 'Created page readback differs', details={'mismatchFields': mismatch})
            return saved
        except Exception as error:
            raise self._unknown('unknown_create', 'Created page readback could not be verified', error, created) from error

    def update(self, before, body, message, scope, title=None, before_effect=None, parent_id=None, allow_unknown_readback=True):
        from .storage import equivalent
        title = before.node.title if title is None else title
        _text(title, 1000)
        _text(message, 4096)
        _need(before.node.status == 'current' and 0 < before.node.version < 2**31 - 1 and isinstance(body, str) and len(body.encode()) <= 4 * 1024 * 1024,
              'invalid_request', 'Published bounded document and change reason required')
        move = None
        if parent_id is not None:
            native_id(parent_id)
            _need(before.node.id != scope.root_page_id and parent_id != before.node.id, 'scope_outside_connection', 'Registered root cannot move')
            source, destination = self._move_chain(scope, before.node.id), self._move_chain(scope, parent_id)
            _need(before.node.id not in {n.id for n in destination} and {n.id for n in source[1:]} <= {n.id for n in destination},
                  'scope_outside_connection', 'Move must preserve every inherited ancestor')
            move = source, destination
        fresh = self.page(before.node.id, before.node, scope, True)
        _need(fresh.node == before.node and fresh.version_message == before.version_message and equivalent(fresh.body, before.body), 'version_conflict', 'Page changed before update')
        # Knowledge edits use the original published body, not an unpublished draft.
        # The separate ensure_no_draft method preserves draft proof for callers that require it.
        if move:
            _need(self._move_chain(scope, before.node.id) == move[0] and self._move_chain(scope, parent_id) == move[1], 'scope_metadata_changed', 'Move ancestry changed')
        account = self.current_user()
        payload = {'id': before.node.id, 'title': title, 'status': 'current', 'body': {'representation': 'storage', 'value': body},
                   'version': {'number': before.node.version + 1, 'message': message}}
        if parent_id is not None:
            payload['parentId'] = parent_id
        if before_effect:
            before_effect()
        try:
            self._request('PUT', self._endpoint('/wiki/api/v2/pages/' + before.node.id), payload)
        except RemoteError as error:
            if not error.outcome_unknown or not allow_unknown_readback:
                raise
        expected = replace(before.node, title=title, parent_id=parent_id if parent_id is not None else before.node.parent_id, version=before.node.version + 1)
        try:
            saved = self.page(before.node.id, expected, scope, True)
            mismatch = self._node_mismatch(saved.node, expected)
            if saved.version_message != message:
                mismatch.append('message')
            if not self._page_equivalent(saved.body, body):
                mismatch.append('content')
            if mismatch:
                raise RemoteError('readback_mismatch', 'Updated page readback differs', details={'mismatchFields': mismatch})
            audit = self.version(saved.node.id, saved.node.version)
            _need(audit['authorId'] == account['accountId'] and audit.get('message', '') == message and self.current_user() == account,
                  'readback_mismatch', 'Updated page native audit differs')
            return saved
        except Exception as error:
            raise self._unknown('unknown_update', 'Updated page readback could not be verified', error) from error

    def comment_location(self, comment_id, kind='footer'):
        native_id(comment_id)
        _need(kind in ('footer', 'inline'), 'invalid_request', 'Invalid native comment kind')
        current, immediate, page, seen = comment_id, None, None, set()
        while True:
            _need(current not in seen and len(seen) < 32, 'comment_limit', 'Cyclic or excessive native comment ancestry')
            seen.add(current)
            raw = self._request('GET', self._endpoint('/wiki/api/v2/' + kind + '-comments/' + current))[1]
            _need(raw.get('id') == current, 'identity_mismatch', 'Native comment identity changed')
            parent = raw.get('parentCommentId')
            if parent is not None:
                native_id(parent)
            if current == comment_id:
                immediate = parent
            claimed = raw.get('pageId')
            if claimed is not None:
                native_id(claimed)
                _need(page is None or page == claimed, 'scope_outside_connection', 'Native comment ancestors disagree about their page')
                page = claimed
            if parent is None:
                _need(page is not None, 'malformed_node', 'Native comment page is unavailable')
                return page, immediate
            current = parent

    def _comment(self, raw, target, kind, expected_parent=None, root=False):
        _need(isinstance(raw, dict) and kind in ('footer', 'inline'))
        selected = native_id(raw.get('id'))
        parent = raw.get('parentCommentId')
        if parent is not None:
            native_id(parent)
        _need(raw.get('pageId') in (None, target) and not any(raw.get(k) is not None for k in ('blogPostId', 'attachmentId', 'customContentId')),
              'scope_outside_connection', 'Comment belongs to another native source')
        _need(not root or raw.get('pageId') == target and parent is None)
        _need(expected_parent is None or parent == expected_parent, 'scope_metadata_changed', 'Native comment parent changed')
        version = raw.get('version')
        _need(isinstance(version, dict))
        number = _positive(version.get('number'))
        editor = _text(version.get('authorId'), 256, True)
        container = raw.get('body')
        _need(isinstance(container, dict), 'missing_storage', 'Native comment storage unavailable')
        storage = container.get('storage', {})
        _need(isinstance(storage, dict) and isinstance(storage.get('value'), str) and storage.get('representation', 'storage') == 'storage', 'missing_storage', 'Native comment storage unavailable')
        result = {'id': selected, 'kind': kind, 'body': storage['value'], 'format': 'storage', 'version': number,
                  'updatedAt': _text(version.get('createdAt'), 100), 'authorId': editor if number == 1 else None,
                  'editorId': editor, 'status': _text(raw.get('status'), 100)}
        if parent is not None:
            result['parentId'] = parent
        if kind == 'inline':
            context = {}
            for name in ('resolutionStatus', 'resolutionLastModifierId', 'resolutionLastModifiedAt'):
                if raw.get(name) is not None:
                    context[name] = _text(raw[name])
            properties = raw.get('properties', {})
            _need(isinstance(properties, dict))
            for name in ('inlineMarkerRef', 'inlineOriginalSelection'):
                if properties.get(name) is not None:
                    _need(isinstance(properties[name], str) and len(properties[name]) <= 65536)
                    context[name] = properties[name]
            result['inlineContext'] = context
        return result

    def _original_author(self, item):
        if item['version'] == 1:
            return item
        try:
            raw = self._request('GET', self._endpoint('/wiki/api/v2/' + item['kind'] + '-comments/' + item['id'] + '/versions/1'))[1]
            _need(type(raw.get('number')) is int and raw['number'] == 1, 'identity_mismatch', 'Original comment version identity differs')
            author = _text(raw.get('authorId'), 256, True)
        except RemoteError as error:
            if error.code not in ('http_403', 'http_404'):
                raise
            author = None
        return dict(item, authorId=author, authorStatus='native_original_version' if author is not None else 'unavailable')

    def list_comments(self, page_id, kind='footer', parent=None, limit=20, cursor=None, scope=None):
        native_id(page_id)
        _need(kind in ('footer', 'inline') and type(limit) is int and 1 <= limit <= 50, 'invalid_request', 'Invalid native comment request')
        observed = self.ancestry(scope, page_id) if scope else [self.scope_node(page_id)]
        if parent is not None:
            location = self.comment_location(native_id(parent), kind)
            _need(location[0] == page_id, 'scope_outside_connection', 'Reply parent belongs to another page')
        path = '/wiki/api/v2/' + (kind + '-comments/' + parent + '/children' if parent else 'pages/' + page_id + '/' + kind + '-comments')
        batch = self.batch(path, {'body-format': 'storage', 'limit': limit}, cursor)
        items = [self._original_author(self._comment(raw, page_id, kind, parent, root=parent is None)) for raw in batch['results']]
        _need(len({i['id'] for i in items}) == len(items), 'malformed_list', 'Duplicate native comment identity')
        if scope:
            self._verify(scope, observed)
        else:
            _need(self.scope_node(page_id) == observed[0], 'scope_metadata_changed', 'Comment parent page changed')
        return dict(batch, results=items)

    def comment_detail(self, target, comment_id, kind='footer', expected_parent_id=None, expected_node=None, scope=None):
        location = self.comment_location(comment_id, kind)
        _need(location == (target, expected_parent_id), 'scope_outside_connection', 'Native comment page or thread differs')
        observed = self.ancestry(scope, target) if scope else [self.scope_node(target)]
        _need(expected_node is None or observed[0] == expected_node, 'scope_metadata_changed', 'Comment parent page changed')
        raw = self._request('GET', self._endpoint('/wiki/api/v2/' + kind + '-comments/' + native_id(comment_id), {'body-format': 'storage'}))[1]
        item = self._original_author(self._comment(raw, target, kind, expected_parent_id))
        _need(item['id'] == comment_id and item.get('parentId') == expected_parent_id, 'identity_mismatch', 'Native comment identity or parent differs')
        _need(self.comment_location(comment_id, kind) == location, 'scope_metadata_changed', 'Native comment thread changed')
        if scope:
            self._verify(scope, observed)
        else:
            _need(self.scope_node(target) == observed[0], 'scope_metadata_changed', 'Comment parent page changed')
        return item

    def comment_inventory(self, target, expected_node=None, scope=None):
        with self.operation(max_requests=256, max_response_bytes=8 * 1024 * 1024, timeout=30):
            observed = self.ancestry(scope, target) if scope else [self.scope_node(target)]
            _need(expected_node is None or observed[0] == expected_node, 'scope_metadata_changed', 'Comment parent page changed')
            comments, identities, size = [], set(), 0
            for kind in ('footer', 'inline'):
                pending = [None]
                while pending:
                    parent, cursor = pending.pop(0), None
                    while True:
                        # Every parent came from this admitted page's native
                        # child list. Retain one graph for the complete poll;
                        # per-comment graph/history reads would spend the whole
                        # request budget before a modest thread was observed.
                        path = '/wiki/api/v2/' + (kind + '-comments/' + parent + '/children' if parent else 'pages/' + target + '/' + kind + '-comments')
                        batch = self.batch(path, {'body-format': 'storage', 'limit': 100}, cursor)
                        for raw in batch['results']:
                            item = self._comment(raw, target, kind, parent, root=parent is None)
                            _need(item['id'] not in identities and len(comments) < 2000, 'comment_limit', 'Duplicate or excessive native comment inventory')
                            identities.add(item['id'])
                            size += len(compact_json(item).encode())
                            _need(size <= 8 * 1024 * 1024, 'comment_limit', 'Native comment inventory exceeds its byte budget')
                            comments.append(item)
                            pending.append(item['id'])
                        cursor = batch.get('cursor')
                        if cursor is None:
                            break
            if scope:
                self._verify(scope, observed)
            else:
                _need(self.scope_node(target) == observed[0], 'scope_metadata_changed', 'Comment parent page changed')
            parent = {'id': target, 'title': observed[0].title, 'status': observed[0].status, 'closed': False}
            return {'provider': 'confluence', 'target': target, 'parent': parent, 'comments': comments}

    def create_comment(self, target, body, reply_to=None, account_id=None, scope=None, kind='footer', selection=None, before_effect=None):
        from .storage import equivalent, inline_selection, selection_anchored
        _need(kind in ('footer', 'inline') and isinstance(body, str) and body.strip() and len(body.encode()) <= 4 * 1024 * 1024,
              'invalid_request', 'Bounded native comment body required')
        page = self.page(target, scope=scope, fresh_storage=True)
        account = self.current_user()
        _need(account_id is None or account_id == account['accountId'], 'account_unavailable', 'Authenticated account changed')
        prior_reply = None
        if reply_to is not None:
            location = self.comment_location(reply_to, kind)
            _need(location[0] == target, 'scope_outside_connection', 'Reply belongs to another page')
            prior_reply = self.comment_detail(target, reply_to, kind, location[1], page.node, scope)
            _need(prior_reply['status'] == 'current', 'scope_not_current', 'Reply target is not current')
        _need(selection is None or kind == 'inline' and reply_to is None and isinstance(selection, tuple) and len(selection) == 3,
              'invalid_selection', 'Invalid inline comment selection')
        payload = {'parentCommentId': native_id(reply_to)} if reply_to else {'pageId': native_id(target)}
        payload['body'] = {'representation': 'storage', 'value': body}
        if selection is not None:
            text, index, count = selection
            _need(type(index) is int and type(count) is int and 0 <= index < count and inline_selection(page.body, text, index + 1) == (index, count), 'invalid_selection', 'Inline selection differs from current native storage')
            payload['inlineCommentProperties'] = {'textSelection': text, 'textSelectionMatchIndex': index, 'textSelectionMatchCount': count}
        _need(self.page(target, page.node, scope, True) == page and self.current_user() == account, 'scope_metadata_changed', 'Page or account changed before commenting')
        if prior_reply:
            _need(self.comment_detail(target, reply_to, kind, prior_reply.get('parentId'), page.node, scope) == prior_reply, 'scope_metadata_changed', 'Reply thread changed')
        if before_effect:
            before_effect()
        raw = self._request('POST', self._endpoint('/wiki/api/v2/' + kind + '-comments'), payload)[1]
        try:
            selected = native_id(raw.get('id'))
        except RemoteError as error:
            raise self._unknown('unknown_create_comment', 'Created native comment ID is unavailable', error) from error
        reference = kind + ':' + selected
        try:
            saved = self.comment_detail(target, selected, kind, reply_to, scope=scope)
            mismatch = [field for field, expected in (('authorId', account['accountId']), ('editorId', account['accountId']), ('version', 1), ('status', 'current')) if saved[field] != expected]
            if not equivalent(saved['body'], body):
                mismatch.append('content')
            if mismatch:
                raise RemoteError('readback_mismatch', 'Created native comment readback differs', details={'mismatchFields': mismatch})
            if selection:
                context = saved.get('inlineContext', {})
                marked = self.page(target, scope=scope, fresh_storage=True)
                _need(replace(marked.node, version=page.node.version) == page.node and marked.node.version - page.node.version in (0, 1) and
                      context.get('inlineOriginalSelection') == selection[0] and context.get('resolutionStatus') == 'open' and
                      selection_anchored(marked.body, selection[0], selection[1], context.get('inlineMarkerRef')),
                      'readback_mismatch', 'Created inline position could not be verified')
            else:
                _need(self.page(target, page.node, scope, True) == page, 'scope_metadata_changed', 'Comment parent changed after creation')
            _need(self.current_user() == account, 'account_unavailable', 'Authenticated account changed after comment')
            return saved
        except Exception as error:
            raise self._unknown('unknown_create_comment', 'Created native comment readback could not be verified', error, reference) from error

    def update_comment(self, target, before, body, scope, before_effect=None):
        from .storage import equivalent
        kind, selected, parent = before.get('kind'), native_id(before.get('id')), before.get('parentId')
        _need(kind in ('footer', 'inline') and before.get('status') == 'current' and isinstance(body, str) and bool(body.strip()), 'invalid_request', 'Current native comment and replacement required')
        number = _positive(before.get('version'))
        _need(number < 2**31 - 1 and len(body.encode()) <= 4 * 1024 * 1024)
        _need(self.comment_detail(target, selected, kind, parent, scope=scope) == before, 'version_conflict', 'Native comment changed before update')
        account = self.current_user()
        if before_effect:
            before_effect()
        self._request('PUT', self._endpoint('/wiki/api/v2/' + kind + '-comments/' + selected), {'version': {'number': number + 1}, 'body': {'representation': 'storage', 'value': body}})
        try:
            saved = self.comment_detail(target, selected, kind, parent, scope=scope)
            mismatch = [name for name in ('authorId', 'parentId', 'inlineContext') if saved.get(name) != before.get(name)]
            mismatch.extend(field for field, expected in (('version', number + 1), ('editorId', account['accountId']), ('status', 'current')) if saved[field] != expected)
            if not equivalent(saved['body'], body):
                mismatch.append('content')
            if mismatch:
                raise RemoteError('readback_mismatch', 'Updated native comment readback differs', details={'mismatchFields': mismatch})
            _need(self.current_user() == account, 'account_unavailable', 'Authenticated account changed after comment')
            return saved
        except Exception as error:
            raise self._unknown('unknown_update_comment', 'Updated native comment readback could not be verified', error) from error

    def close(self):
        """Requests retain no owned worker or persistent connection."""
