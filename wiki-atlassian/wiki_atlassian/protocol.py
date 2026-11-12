"""Bounded UTF-8 JSON-RPC stdio; stdout contains protocol responses only."""
from __future__ import annotations

import contextlib
import json
import math
import sys

from . import __version__
from .models import RemoteError, compact_json
from .tools import PERSONAL_TOOL, WIKI_TOOLS, validate_arguments


MAX_LINE = 1024 * 1024
MAX_OUTPUT = 2 * 1024 * 1024


class Server:
    def __init__(self, documents=None, personal=None, project_unavailable=False):
        self.documents, self.personal = documents, personal
        self.project_unavailable = project_unavailable
        self.tools = ([*WIKI_TOOLS] if documents is not None else [WIKI_TOOLS[0]] if project_unavailable else []) + ([PERSONAL_TOOL] if personal is not None else [])

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
                            'Use read_personal_feedback for the separate privately configured Harness inbox when available.')
            if self.project_unavailable:
                instructions = 'Project wiki is unavailable. The separate private inbox remains readable; never borrow project credentials.'
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
                if name == 'read_personal_feedback':
                    value = self.personal(args)
                elif self.project_unavailable:
                    value = {'status': 'error', 'code': 'connection_unavailable', 'message': 'Correct project configuration/authentication separately from the private inbox'}
                else:
                    value = self.documents.execute(name, args)
            encoded = compact_json(value)
            if len(encoded.encode('utf-8')) > MAX_OUTPUT:
                raise ValueError('Output exceeds protocol budget')
            return self.result(id, {'content': [{'type': 'text', 'text': encoded}]})
        except Exception:
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
