"""Bounded UTF-8 JSON-RPC stdio; stdout contains protocol responses only."""
from __future__ import annotations

import contextlib
from concurrent.futures import ThreadPoolExecutor
from html import escape
import json
import math
import re
import sys
import threading
import uuid

from . import __version__
from .models import RemoteError, compact_json
from .policy import reject_secrets
from .tools import JIRA_TOOLS, JIRA_TOOL_NAMES, REVIEW_TOOLS, WIKI_TOOLS, validate_arguments


MAX_LINE = 1024 * 1024
MAX_OUTPUT = 2 * 1024 * 1024
MUTATIONS = frozenset(('create_document', 'update_document', 'create_comment', 'update_comment',
                       'add_jira_comment', 'transition_jira_issue'))
RECEIPT_LIMIT = 64


def valid_id(value):
    return type(value) in (str, int) or type(value) is float and math.isfinite(value)


class Server:
    def __init__(self, documents=None, personal=None, project_unavailable=False, *, reviewer=False, codex_review=None,
                 jira=None):
        self.documents, self.personal = documents, None if reviewer else personal
        self.reviewer = reviewer
        self.jira = None if reviewer else jira
        self.project_unavailable = project_unavailable
        self.tools = ([*WIKI_TOOLS] if documents is not None or personal is not None
                      else [WIKI_TOOLS[0]] if project_unavailable else [])
        if reviewer:
            self.tools = [tool for tool in self.tools if documents is not None and tool['name'] in REVIEW_TOOLS]
        if self.jira is not None:
            self.tools.extend(JIRA_TOOLS)
        self._receipts = {}
        self._receipt_lock = threading.Condition()
        self._submit_update = None
        self._submit_review = None
        self._codex_review = None if reviewer else codex_review

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
        if name in JIRA_TOOL_NAMES:
            value = self.jira.execute(name, args)
        elif connection == 'personal' and self.personal is not None:
            value = self.personal(name, args)
        elif connection == 'project' and self.documents is not None:
            value = self.documents.execute(name, args)
            if (name in ('create_document', 'update_document') and value.get('status') in ('created', 'updated')
                    and not value.get('authorizationDenied')):
                value = {**value, 'reviewInstruction': (
                    'Before completing this authoring task, invoke the independent wiki-atlassian document reviewer '
                    'with this saved page ID, version and its sources. Batch related saved revisions in one review. '
                    'If the host has no restricted reviewer, report the pending review.')}
                if self._codex_review is not None:
                    review = self._schedule_review(value, args)
                    value.update(documentReview=review, reviewInstruction=review['next'])
        else:
            value = {'status': 'error', 'code': 'connection_unavailable',
                     'message': 'The selected connection is unavailable; inspect its own settings and authentication. No fallback was used.'}
        return {**value, 'connection': connection}

    @staticmethod
    def _unknown(name):
        return {'status': 'unknown', 'code': 'unknown_outcome', 'operation': name,
                'next': 'Inspect native content/history using the same connection. Do not repeat the write.'}

    def _queue_operation(self, receipt, submit, task):
        if submit is None:
            return None
        with self._receipt_lock:
            oldest = None
            if len(self._receipts) >= RECEIPT_LIMIT:
                oldest = next((key for key, value in self._receipts.items() if value['status'] == 'finished'), None)
                if oldest is None:
                    return None
            if not submit(task):
                return None
            if oldest is not None:
                del self._receipts[oldest]
            operation_id = uuid.uuid4().hex
            self._receipts[operation_id] = receipt
            return operation_id

    def _schedule_review(self, saved, edit=None):
        target = {key: saved.get(key) for key in ('id', 'version', 'space')}
        # Pass only native revision and section locators. Free text in an
        # authoring request is neither reviewed evidence nor reviewer instructions.
        if edit:
            sections = [row.get('section') for row in [edit, *edit.get('edits', [])] if isinstance(row, dict)]
            sections = [key for key in sections if isinstance(key, str) and len(key) <= 30
                        and re.fullmatch(r'[1-9][0-9]*(?:\.[1-9][0-9]*)*', key)]
            if sections:
                target['sections'] = list(dict.fromkeys(sections))[:10]
        if saved.get('basedOn'):
            target['sources'] = saved['basedOn']
        blocked = dict(status='BLOCKED_ENV', operationId=None,
                       next='The native save succeeded, but the independent reviewer could not start. Report the pending review.')
        if (not isinstance(target['id'], str) or not re.fullmatch(r'[1-9][0-9]{0,31}', target['id'])
                or type(target['version']) is not int or not 1 <= target['version'] <= 2147483647
                or not isinstance(target['space'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', target['space'])):
            return blocked
        receipt = dict(status='queued', connection='project', operation='document_review', target=target)

        def finish_review():
            with self._receipt_lock:
                receipt['status'] = 'running'
            try:
                result = self._codex_review(dict(target))
                if result.get('status') not in ('PASS', 'FAIL', 'BLOCKED_ENV'):
                    raise ValueError('Invalid reviewer outcome')
                if result['status'] in ('PASS', 'FAIL') and (
                    result.get('document') != target['id'] or type(result.get('version')) is not int
                    or result['version'] != target['version']
                ):
                    raise ValueError('Reviewer must identify the verified revision')
                outcome = {'status': result['status'], 'document': target['id'], 'version': target['version']}
                summary = result.get('summary')
                if isinstance(summary, str) and summary:
                    if len(summary) > 4096:
                        raise ValueError('Reviewer summary exceeds receipt bound')
                    reject_secrets(escape(summary))
                    outcome['summary'] = summary
                session_id = result.get('sessionId')
                if isinstance(session_id, str):
                    outcome['sessionId'] = str(uuid.UUID(session_id))
            except Exception:
                outcome = {'status': 'BLOCKED_ENV', 'code': 'review_unavailable'}
            with self._receipt_lock:
                receipt.update(status='finished', outcome=outcome)
                self._receipt_lock.notify_all()

        operation_id = self._queue_operation(receipt, self._submit_review, finish_review)
        if operation_id is None:
            return blocked
        return dict(status='queued', operationId=operation_id, next=(
            'The saved revision is queued for an independent document reviewer. '
            'Call operation_status with this documentReview.operationId and waitSeconds=20 in this session '
            'until finished. Report its outcome before completing the authoring task.'))

    def _operation_status(self, operation_id, connection, wait_seconds=0):
        if len(operation_id) != 32 or any(c not in '0123456789abcdef' for c in operation_id):
            return {'status': 'error', 'code': 'invalid_operation_id'}
        with self._receipt_lock:
            receipt = self._receipts.get(operation_id)
            if receipt is not None and receipt['connection'] == connection:
                if receipt.get('operation') != 'document_review':
                    return {'operationId': operation_id, **receipt}
                if wait_seconds:
                    self._receipt_lock.wait_for(lambda: receipt['status'] == 'finished', timeout=wait_seconds)
                public = dict(operationId=operation_id, connection=connection, operation='document_review', status=receipt['status'])
                if receipt['status'] != 'finished':
                    return public
                outcome, target = receipt['outcome'], receipt['target']
            else:
                return {**self._unknown('update_document'), 'operationId': operation_id,
                        'code': 'operation_unavailable',
                        'message': 'This operation is unavailable in this session and connection.'}
        # Authorization can change after the reviewer finished. Read native
        # metadata again before disclosing the review on every delivery.
        try:
            read = self.documents.execute('get_document', dict(document=target['id'], version=target['version'],
                                          space=target['space'], metadata=True, includeBody=False))
            if (read.get('status') == 'read' and not read.get('authorizationDenied') and read.get('id') == target['id']
                    and read.get('space') == target['space'] and read.get('version') == target['version']):
                return {**public, 'outcome': outcome}
        except Exception:
            pass
        return {**public, 'outcome': {'status': 'BLOCKED_ENV', 'code': 'review_access_unconfirmed'}}

    def _defer_update(self, args, connection):
        if not args.get('document') or args['document'] in ('feedback', 'memory') or args.get('version', 0) < 1 or args.get('includeBody'):
            return {'status': 'error', 'code': 'invalid_edit', 'requestStarted': False,
                    'message': 'Deferred updates require an explicit document, expected version and no included body.'}
        submit = self._submit_update
        if submit is None:
            return self.busy()
        receipt = {'status': 'queued', 'connection': connection}

        def finish_update():
            with self._receipt_lock:
                receipt['status'] = 'running'
            try:
                outcome = self._execute('update_document', args, connection)
            except Exception:
                outcome = self._unknown('update_document')
            fields = ('status', 'code', 'message', 'id', 'version', 'url', 'space', 'spaceId',
                      'syncRequired', 'authorizationDenied', 'details', 'next', 'requestHash', 'reviewInstruction', 'documentReview')
            with self._receipt_lock:
                receipt.update(status='finished', outcome={key: outcome[key] for key in fields if key in outcome})

        operation_id = self._queue_operation(receipt, submit, finish_update)
        if operation_id is None:
            return self.busy()
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
                            'After related project document writes are verified, route the saved revisions and sources together to the wiki-atlassian document reviewer. '
                            'Use the same tools with connection: personal for the separate privately configured page. '
                            'Discover its page ID with connection_status(connection: personal). Personal access covers that exact page and its comments, never descendants. '
                            'Omitting connection selects project; unavailable connections never borrow credentials.')
            if self.reviewer:
                instructions = 'Read stored project evidence and its sources with the available document review tools.'
            elif self._codex_review is not None:
                instructions += (' For a returned documentReview, use its operation_status receipt in this same '
                                 'session and report the finished review outcome.')
            if not self.reviewer and self.documents is not None:
                instructions += (' In connection_status, pending or busy means synchronization has not finished; '
                                 'report that progress and check again before calling the connection unavailable. '
                                 'For failed or unavailable, report the returned connection or space error.')
            if self.jira is not None:
                instructions += (' For configured Jira projects use get_jira_issue, add_jira_comment, '
                                 'list_jira_transitions and transition_jira_issue on this project connection. '
                                 'Inspect uncertain writes before retrying.')
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
                value = self._operation_status(args['operationId'], connection or 'project', args['waitSeconds'])
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
        reviews = threading.BoundedSemaphore(4)
        review_polls = threading.BoundedSemaphore(4)

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
            # Writes may schedule reviews while draining at EOF. Keep the review
            # executor alive until the write executor has finished its last job.
            with contextlib.ExitStack() as cleanup, \
                 ThreadPoolExecutor(max_workers=2, thread_name_prefix='wiki-review') as review_pool, \
                 ThreadPoolExecutor(max_workers=4, thread_name_prefix='wiki-review-poll') as poll_pool, \
                 ThreadPoolExecutor(max_workers=4, thread_name_prefix='wiki-read') as read_pool, \
                 ThreadPoolExecutor(max_workers=1, thread_name_prefix='wiki-write') as write_pool:
                self._submit_update = lambda task: submit(write_pool, writes, task)
                self._submit_review = lambda task: submit(review_pool, reviews, task)
                cleanup.callback(setattr, self, '_submit_review', None)
                try:
                    for request in self._requests(reader, send):
                        params = request.get('params') if isinstance(request, dict) else None
                        name = params.get('name') if isinstance(params, dict) and request.get('method') == 'tools/call' else None
                        args = params.get('arguments') if isinstance(params, dict) else None
                        id = request.get('id') if isinstance(request, dict) else None
                        if valid_id(id) and name == 'operation_status' and self._codex_review is not None and isinstance(args, dict):
                            operation_id = args.get('operationId')
                            with self._receipt_lock:
                                receipt = self._receipts.get(operation_id) if isinstance(operation_id, str) else None
                                review_poll = receipt and receipt.get('operation') == 'document_review' and (
                                    receipt['connection'] == args.get('connection', 'project'))
                            if review_poll:
                                if not submit(poll_pool, review_polls, lambda request=request: send(self.handle(request))):
                                    send(self.tool_result(id, self.busy()))
                                continue
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
