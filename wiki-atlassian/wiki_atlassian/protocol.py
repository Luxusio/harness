"""Bounded UTF-8 JSON-RPC stdio; stdout contains protocol responses only."""
from __future__ import annotations

import contextlib
from concurrent.futures import ThreadPoolExecutor
import json
import math
import sys
import threading
import uuid

from . import __version__
from .models import RemoteError, compact_json
from .tools import REVIEW_TOOLS, WIKI_TOOLS, validate_arguments


MAX_LINE = 1024 * 1024
MAX_OUTPUT = 2 * 1024 * 1024
MUTATIONS = frozenset(('create_document', 'update_document', 'create_comment', 'update_comment'))
RECEIPT_LIMIT = 64


def valid_id(value):
    return type(value) in (str, int) or type(value) is float and math.isfinite(value)


class Server:
    def __init__(self, documents=None, personal=None, project_unavailable=False, *, reviewer=False):
        self.documents, self.personal = documents, None if reviewer else personal
        self.reviewer = reviewer
        self.project_unavailable = project_unavailable
        self.tools = ([*WIKI_TOOLS] if documents is not None or personal is not None
                      else [WIKI_TOOLS[0]] if project_unavailable else [])
        if reviewer:
            self.tools = [tool for tool in self.tools if documents is not None and tool['name'] in REVIEW_TOOLS]
        self._receipts = {}
        self._receipt_lock = threading.Lock()
        self._submit_update = None

    @staticmethod
    def error(id, code, message):
        return {'jsonrpc': '2.0', 'id': id, 'error': {'code': code, 'message': message}}

    @staticmethod
    def result(id, value):
        return {'jsonrpc': '2.0', 'id': id, 'result': value}

    @staticmethod
    def busy():
        return {'status': 'error', 'code': 'server_busy', 'requestStarted': False,
                'message': 'The server lane is full. Retry after an outstanding call finishes.'}

    def tool_result(self, id, value):
        encoded = compact_json(value)
        if len(encoded.encode('utf-8')) > MAX_OUTPUT:
            raise ValueError('Output exceeds protocol budget')
        return self.result(id, {'content': [{'type': 'text', 'text': encoded}]})

    def _execute(self, name, args, connection):
        if name == 'connection_status' and connection is None:
            return {'connections': {
                'project': self.documents.execute(name, args) if self.documents is not None else {
                    'configured': False, 'status': 'unavailable' if self.project_unavailable else 'unconfigured'},
                'personal': {'configured': self.personal is not None, 'authentication': 'not_checked'}}}
        connection = connection or 'project'
        if connection == 'personal' and self.personal is not None:
            value = self.personal(name, args)
        elif connection == 'project' and self.documents is not None:
            value = self.documents.execute(name, args)
        else:
            value = {'status': 'error', 'code': 'connection_unavailable',
                     'message': 'The selected connection is unavailable; inspect its own settings and authentication. No fallback was used.'}
        return {**value, 'connection': connection}

    @staticmethod
    def _unknown(name):
        return {'status': 'unknown', 'code': 'unknown_outcome', 'operation': name,
                'next': 'Inspect native content/history using the same connection. Do not repeat the write.'}

    def _operation_status(self, operation_id, connection):
        if len(operation_id) != 32 or any(c not in '0123456789abcdef' for c in operation_id):
            return {'status': 'error', 'code': 'invalid_operation_id'}
        with self._receipt_lock:
            receipt = self._receipts.get(operation_id)
            if receipt is not None and receipt['connection'] == connection:
                return {'operationId': operation_id, **receipt}
        return {**self._unknown('update_document'), 'operationId': operation_id,
                'code': 'operation_unavailable',
                'message': 'This operation is unavailable in this session and connection.'}

    def _defer_update(self, args, connection):
        if not args.get('document') or args['document'] in ('feedback', 'memory') or args.get('version', 0) < 1 or args.get('includeBody'):
            return {'status': 'error', 'code': 'invalid_edit', 'requestStarted': False,
                    'message': 'Deferred updates require an explicit document, expected version and no included body.'}
        submit = self._submit_update
        if submit is None:
            return self.busy()
        operation_id = uuid.uuid4().hex
        receipt = {'status': 'queued', 'connection': connection}

        def finish_update():
            with self._receipt_lock:
                receipt['status'] = 'running'
            try:
                outcome = self._execute('update_document', args, connection)
            except Exception:
                outcome = self._unknown('update_document')
            fields = ('status', 'code', 'message', 'id', 'version', 'url', 'space', 'spaceId',
                      'syncRequired', 'authorizationDenied', 'details', 'next', 'requestHash')
            with self._receipt_lock:
                receipt.update(status='finished', outcome={key: outcome[key] for key in fields if key in outcome})

        with self._receipt_lock:
            oldest = None
            if len(self._receipts) >= RECEIPT_LIMIT:
                oldest = next((key for key, value in self._receipts.items() if value['status'] == 'finished'), None)
                if oldest is None:
                    return self.busy()
            if not submit(finish_update):
                return self.busy()
            if oldest is not None:
                del self._receipts[oldest]
            self._receipts[operation_id] = receipt
        return {'status': 'queued', 'operationId': operation_id, 'connection': connection,
                'next': 'Continue independent work. Use operation_status before relying on the write.'}

    def handle(self, request):
        if not isinstance(request, dict):
            return self.error(None, -32600, 'Invalid request')
        id = request.get('id')
        if id is not None and not valid_id(id):
            return self.error(None, -32600, 'Invalid request ID')
        method = request.get('method')
        if not isinstance(method, str) or not method.strip():
            return self.error(id, -32600, 'Missing or invalid method')
        if request.get('jsonrpc', '2.0') != '2.0':
            return self.error(id, -32600, 'Invalid JSON-RPC version')
        if id is None:
            return None
        params = request.get('params', {})
        if not isinstance(params, dict):
            return self.error(id, -32602, 'params must be an object')
        if method == 'initialize':
            instructions = ('Use atlassian MCP for the configured spaces. Shared replicas synchronize automatically; '
                            'do not edit replicas. Select an explicit space or native parent for new documents. '
                            'In one project space, batch related terms with search_documents {"queries": ["term", "ID"]}. '
                            'For current guide openings or exact evidence, call get_document with {"reads": [{"document": "123", "length": 2000}]} or pass the returned read locators in reads. '
                            'Deferred updates return a same-session ID; verify the outcome with operation_status. '
                            'Use the same tools with connection: personal for the separate privately configured page. '
                            'Discover its page ID with connection_status(connection: personal). Personal access covers that exact page and its comments, never descendants. '
                            'Omitting connection selects project; unavailable connections never borrow credentials.')
            if self.reviewer:
                instructions = 'Read stored project evidence and its sources with the available document review tools.'
            return self.result(id, {'protocolVersion': '2024-11-05', 'serverInfo': {'name': 'atlassian', 'version': __version__},
                                    'capabilities': {'tools': {}}, 'instructions': instructions})
        if method == 'ping':
            return self.result(id, {})
        if method == 'tools/list':
            return self.result(id, {'tools': self.tools})
        if method != 'tools/call':
            return self.error(id, -32601, 'Method not found')
        name, args = params.get('name'), params.get('arguments', {})
        if not isinstance(name, str) or name not in {tool['name'] for tool in self.tools}:
            return self.error(id, -32602, 'Unknown or unavailable tool')
        try:
            args = validate_arguments(name, args)
        except RemoteError as error:
            return self.error(id, -32602, str(error))
        try:
            connection = args.pop('connection', None)
            if name == 'operation_status':
                value = self._operation_status(args['operationId'], connection or 'project')
            elif name == 'update_document' and args.pop('defer', False):
                value = self._defer_update(args, connection or 'project')
            else:
                value = self._execute(name, args, connection)
            return self.tool_result(id, value)
        except Exception:
            if name in MUTATIONS:
                return self.tool_result(id, self._unknown(name))
            return self.result(id, {'content': [{'type': 'text', 'text': 'Tool execution unavailable; inspect connection status and private settings without exposing secrets'}], 'isError': True})

    def run(self, reader=None, writer=None):
        reader = sys.stdin.buffer if reader is None else reader
        writer = sys.stdout.buffer if writer is None else writer
        write_lock = threading.Lock()
        reads = threading.BoundedSemaphore(12)
        writes = threading.BoundedSemaphore(8)

        def send(response):
            if response is not None:
                encoded = (compact_json(response) + '\n').encode('utf-8')
                with write_lock:
                    writer.write(encoded)
                    writer.flush()

        def submit(executor, slots, task):
            if not slots.acquire(blocking=False):
                return False

            def bounded_task():
                try:
                    task()
                finally:
                    slots.release()

            try:
                executor.submit(bounded_task)
            except RuntimeError:
                slots.release()
                return False
            return True

        # Capture the real protocol stream before redirecting logs. Per-call
        # stdout redirection is process-global and unsafe with concurrent reads.
        with contextlib.redirect_stdout(sys.stderr):
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix='wiki-read') as read_pool, \
                 ThreadPoolExecutor(max_workers=1, thread_name_prefix='wiki-write') as write_pool:
                self._submit_update = lambda task: submit(write_pool, writes, task)
                try:
                    for request in self._requests(reader, send):
                        params = request.get('params') if isinstance(request, dict) else None
                        name = params.get('name') if isinstance(params, dict) and request.get('method') == 'tools/call' else None
                        args = params.get('arguments') if isinstance(params, dict) else None
                        id = request.get('id') if isinstance(request, dict) else None
                        if not valid_id(id) or not isinstance(name, str) or name == 'operation_status' or (
                            name == 'update_document' and isinstance(args, dict) and args.get('defer') is True
                        ):
                            send(self.handle(request))
                        else:
                            pool, slots = (write_pool, writes) if name in MUTATIONS else (read_pool, reads)
                            if not submit(pool, slots, lambda request=request: send(self.handle(request))):
                                send(self.tool_result(id, self.busy()))
                finally:
                    self._submit_update = None

    def _requests(self, reader, send):
        while True:
            line = reader.readline(MAX_LINE + 1)
            if not line:
                break
            if len(line) > MAX_LINE:
                while line and not line.endswith(b'\n'):
                    line = reader.readline(MAX_LINE + 1)
                send(self.error(None, -32700, 'Request exceeds the supported line bound'))
            elif line.strip():
                try:
                    request = json.loads(line.decode('utf-8'), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                except (ValueError, UnicodeError, RecursionError):
                    send(self.error(None, -32700, 'Parse error'))
                else:
                    yield request
