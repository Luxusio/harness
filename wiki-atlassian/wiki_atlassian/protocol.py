"""Bounded UTF-8 JSON-RPC stdio; stdout contains protocol responses only."""
from __future__ import annotations

import contextlib
import json
import math
import sys

from . import __version__
from .models import RemoteError, compact_json
from .tools import WIKI_TOOLS, validate_arguments


MAX_LINE = 1024 * 1024
MAX_OUTPUT = 2 * 1024 * 1024


class Server:
    def __init__(self, documents=None, personal=None, project_unavailable=False):
        self.documents, self.personal = documents, personal
        self.project_unavailable = project_unavailable
        self.tools = ([*WIKI_TOOLS] if documents is not None or personal is not None
                      else [WIKI_TOOLS[0]] if project_unavailable else [])

    @staticmethod
    def error(id, code, message):
        return {'jsonrpc': '2.0', 'id': id, 'error': {'code': code, 'message': message}}

    @staticmethod
    def result(id, value):
        return {'jsonrpc': '2.0', 'id': id, 'result': value}

    def handle(self, request):
        if not isinstance(request, dict):
            return self.error(None, -32600, 'Invalid request')
        id = request.get('id')
        if id is not None and (type(id) not in (str, int, float) or type(id) is float and not math.isfinite(id)):
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
                            'Use the same tools with connection: personal for the separate privately configured page. '
                            'Discover its page ID with connection_status(connection: personal). Personal access covers that exact page and its comments, never descendants. '
                            'Omitting connection selects project; unavailable connections never borrow credentials.')
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
        except RemoteError:
            return self.error(id, -32602, 'Tool arguments do not match the declared schema')
        try:
            with contextlib.redirect_stdout(sys.stderr):
                connection = args.pop('connection', None)
                if name == 'connection_status' and connection is None:
                    value = {'connections': {
                        'project': self.documents.execute(name, args) if self.documents is not None else {
                            'configured': False, 'status': 'unavailable' if self.project_unavailable else 'unconfigured'},
                        'personal': {'configured': self.personal is not None, 'authentication': 'not_checked'}}}
                else:
                    connection = connection or 'project'
                    if connection == 'personal' and self.personal is not None:
                        value = self.personal(name, args)
                    elif connection == 'project' and self.documents is not None:
                        value = self.documents.execute(name, args)
                    else:
                        value = {'status': 'error', 'code': 'connection_unavailable',
                                 'message': 'The selected connection is unavailable; inspect its own settings and authentication. No fallback was used.'}
                    value = {**value, 'connection': connection}
            encoded = compact_json(value)
            if len(encoded.encode('utf-8')) > MAX_OUTPUT:
                raise ValueError('Output exceeds protocol budget')
            return self.result(id, {'content': [{'type': 'text', 'text': encoded}]})
        except Exception:
            if name in ('create_document', 'update_document', 'create_comment', 'update_comment'):
                value = {'status': 'unknown', 'code': 'unknown_outcome', 'operation': name,
                         'next': 'Inspect native content/history using the same connection. Do not repeat the write.'}
                return self.result(id, {'content': [{'type': 'text', 'text': compact_json(value)}]})
            return self.result(id, {'content': [{'type': 'text', 'text': 'Tool execution unavailable; inspect connection status and private settings without exposing secrets'}], 'isError': True})

    def run(self, reader=None, writer=None):
        reader = sys.stdin.buffer if reader is None else reader
        writer = sys.stdout.buffer if writer is None else writer
        while True:
            line = reader.readline(MAX_LINE + 1)
            if not line:
                break
            if len(line) > MAX_LINE:
                while line and not line.endswith(b'\n'):
                    line = reader.readline(MAX_LINE + 1)
                response = self.error(None, -32700, 'Request exceeds the supported line bound')
            elif not line.strip():
                continue
            else:
                try:
                    request = json.loads(line.decode('utf-8'), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                    response = self.handle(request)
                except (ValueError, UnicodeError, RecursionError):
                    response = self.error(None, -32700, 'Parse error')
                except Exception:
                    response = self.error(None, -32603, 'Internal error')
            if response is not None:
                writer.write((compact_json(response) + '\n').encode('utf-8'))
                writer.flush()
