"""Construct the project connection and independent private inbox once."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from .config import Project, home_directory, personal_settings
from .models import Credentials, RemoteError, compact_json
from .protocol import MAX_LINE, MAX_OUTPUT, Server
from .tools import validate_arguments


def terminate(signum, frame):
    raise SystemExit(1)


def personal_operation(operation, arguments, timeout=60):
    """Bound one common operation, including private mise evaluation and HTTPS."""
    from native_cli import owned_python_argv
    from wiki_platform import run_bounded
    environment = dict(os.environ)
    # Native command lines/environment blocks cannot transport large document
    # bodies. The existing owned runner supplies bounded stdin instead.
    request = compact_json(dict(operation=operation, arguments=arguments,
                                deadline=time.monotonic() + timeout)).encode('utf-8')
    try:
        if len(request) > MAX_LINE:
            return {'status': 'error', 'code': 'request_limit', 'message': 'The operation exceeds the supported request bound; no write was sent.'}
        root = Path(__file__).resolve().parents[1]
        launcher = root / 'scripts' / 'launch.py'
        import personal_feedback
        command = owned_python_argv(sys.executable, launcher, ['--personal-operation'],
                                    import_roots=[Path(personal_feedback.__file__).resolve().parent])
        result = run_bounded(command, cwd=Path.cwd(), env=environment, timeout=timeout,
                             limit=MAX_OUTPUT, graceful=True, input_bytes=request)
        value = json.loads(result.stdout.decode('utf-8'))
        if result.returncode != 0 or not isinstance(value, dict):
            raise ValueError()
        return value
    except (OSError, ValueError, subprocess.SubprocessError):
        if operation in ('create_document', 'update_document', 'create_comment', 'update_comment'):
            return {'status': 'unknown', 'code': 'unknown_outcome', 'operation': operation,
                    'next': 'The private operation did not return a verified result. Inspect native content/history with connection: personal; never automatically repeat the write.'}
        return {'status': 'unavailable', 'code': 'connection_unavailable',
                'message': 'The private operation could not finish within its supported execution bounds; check private access and service availability.'}


def execute_personal(operation, data, deadline):
    """Use the normal native service under the exact private page selection."""
    import personal_feedback
    from .remote import Remote
    from .replica import Replica
    from .service import WikiService
    remote = replica = None
    started = False
    try:
        data = validate_arguments(operation, data)
        data.pop('connection', None)
        home = home_directory()
        selected = personal_feedback.load_destination(home)
        if selected['status'] != 'configured':
            return selected
        values = personal_feedback._credentials(home / '.config/harness', dict(os.environ), deadline=deadline)
        if ':' in values['ATLASSIAN_EMAIL']:
            raise RemoteError('connection_unavailable', 'Private account authentication is invalid')
        credentials = Credentials(values['ATLASSIAN_BASE_URL'], values['ATLASSIAN_EMAIL'], values['ATLASSIAN_API_TOKEN'])
        remote = Remote(credentials)
        with remote.operation(timeout=max(.001, min(45, deadline - time.monotonic()))):
            settings = personal_settings(selected, credentials, remote, home / '.local/share/wiki-atlassian')
            if 'space' in data and data.pop('space') != settings.space:
                raise RemoteError('space_mismatch', 'The explicit space differs from the privately configured page')
            for field in ('document', 'target'):
                if data.get(field) == 'feedback':
                    data[field] = settings.root_page_id
            replica = Replica(settings, remote)
            if operation == 'connection_status':
                result = dict(ready=True, status='connected', scope='page',
                              page=dict(id=settings.root_page_id, url=selected['page']),
                              replica=replica.status())
            else:
                service = WikiService(settings, remote, replica)
                service.validate_selection(operation, data)
                # Comments and history do not require a full document replica.
                # Read/search synchronization reuses the existing version/interval
                # rules, never traverses children or watches all comment bodies.
                if operation in ('get_document', 'search_documents', 'document_relations'):
                    replica.synchronize()
                started = True
                result = service.execute(operation, data)
            if credentials.api_token in compact_json(result):
                raise RemoteError('private_content', 'The native response contains authentication material and was withheld')
            return result
    except Exception as exc:
        unknown = started and operation in ('create_document', 'update_document', 'create_comment', 'update_comment')
        value = dict(status='unknown' if unknown else 'error', code='unknown_outcome' if unknown else getattr(exc, 'code', 'connection_unavailable'),
                     message=str(exc) if isinstance(exc, (RemoteError, personal_feedback.FeedbackError)) else 'The private operation could not be admitted')
        if unknown:
            value['next'] = 'Inspect native content/history with connection: personal; do not repeat the write.'
        return value
    finally:
        if replica is not None:
            replica.close()
        if remote is not None:
            remote.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Native Python Atlassian wiki MCP (stdio)')
    parser.add_argument('--project-root', type=Path, default=Path.cwd())
    parser.add_argument('--personal-operation', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not args.project_root.is_absolute() or not args.project_root.is_dir():
        parser.error('--project-root must be an existing absolute startup directory')
    if args.personal_operation:
        previous_handler = signal.signal(signal.SIGTERM, terminate)
        try:
            raw = sys.stdin.buffer.read(MAX_LINE + 1)
            if len(raw) > MAX_LINE:
                raise ValueError('Private operation request exceeds its byte bound')
            request = json.loads(raw.decode('utf-8'))
            if (not isinstance(request, dict) or set(request) != {'operation', 'arguments', 'deadline'}
                    or type(request['deadline']) not in (int, float) or not math.isfinite(request['deadline'])
                    or not 0 < request['deadline'] - time.monotonic() <= 60):
                raise ValueError('Invalid private operation request')
            value = execute_personal(request['operation'], request['arguments'], request['deadline'])
            sys.stdout.buffer.write(compact_json(value).encode('utf-8'))
            return 0
        finally:
            signal.signal(signal.SIGTERM, previous_handler)
    # This helper is packaged beside this code, never discovered in project cwd.
    import personal_feedback
    home = home_directory()
    private_path = home / '.config/harness/feedback.yaml'
    personal = personal_operation if os.path.lexists(private_path) else None
    documents = None
    unavailable = False
    try:
        project = Project.from_project(args.project_root)
    except ValueError:
        if personal is None:
            sys.stderr.write('Atlassian project connection or authentication unavailable\n')
            return 1
        project, unavailable = None, True
    previous_handler = signal.signal(signal.SIGTERM, terminate)
    try:
        if project is not None:
            from .remote import Remote
            from .multispace import MultiSpaceKnowledge
            documents = MultiSpaceKnowledge(project, Remote(project.credentials))
        Server(documents, personal, unavailable).run()
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
        if documents is not None:
            documents.close()
    return 0
