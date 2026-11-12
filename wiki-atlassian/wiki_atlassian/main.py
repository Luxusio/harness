"""Construct the project connection and independent private inbox once."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from .config import Project
from .protocol import MAX_OUTPUT, Server


def terminate(signum, frame):
    raise SystemExit(1)


def read_personal(values, timeout=20):
    """Bound the entire private read, including mise, DNS and slow HTTP bodies."""
    import personal_feedback
    from wiki_platform import run_bounded
    environment = dict(os.environ)
    # Private continuation data stays out of process listings and persistent files.
    environment['_HARNESS_PRIVATE_READ_REQUEST'] = json.dumps(dict(values=values, deadline=time.monotonic()+timeout))
    code = """import json,os,signal,sys
sys.path.insert(0,sys.argv[1])
import personal_feedback
def terminate(signum,frame):
    raise SystemExit(1)
signal.signal(signal.SIGTERM,terminate)
request=json.loads(os.environ.pop('_HARNESS_PRIVATE_READ_REQUEST'))
value=personal_feedback.perform('read',request=request['values'],deadline=request['deadline'])
sys.stdout.buffer.write(json.dumps(value,ensure_ascii=False).encode('utf-8'))
"""
    try:
        result = run_bounded([sys.executable, '-I', '-B', '-c', code,
                              str(Path(personal_feedback.__file__).resolve().parent)],
                             cwd=Path.cwd(), env=environment, timeout=timeout, limit=MAX_OUTPUT, graceful=True)
        value = json.loads(result.stdout.decode('utf-8'))
        if result.returncode != 0 or not isinstance(value, dict):
            raise ValueError()
        return value
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'status': 'unavailable', 'message': 'Private inbox read could not finish within its supported execution bounds; check private access and service availability.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Native Python Atlassian wiki MCP (stdio)')
    parser.add_argument('--project-root', type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    if not args.project_root.is_absolute() or not args.project_root.is_dir():
        parser.error('--project-root must be an existing absolute startup directory')
    # This helper is packaged beside this code, never discovered in project cwd.
    import personal_feedback
    from .config import home_directory
    home = home_directory()
    private_path = home / '.config/harness/feedback.yaml'
    personal = read_personal if os.path.lexists(private_path) else None
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
