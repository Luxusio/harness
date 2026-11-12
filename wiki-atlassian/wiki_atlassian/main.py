"""Construct the project connection and independent private inbox once."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from .config import Project
from .protocol import Server


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
    personal = (lambda values: personal_feedback.perform('read', home=home, request=values)) if os.path.lexists(private_path) else None
    documents = None
    unavailable = False
    try:
        project = Project.from_project(args.project_root)
    except ValueError:
        if personal is None:
            sys.stderr.write('Atlassian project connection or authentication unavailable\n')
            return 1
        project, unavailable = None, True
    try:
        if project is not None:
            from .remote import Remote
            from .multispace import MultiSpaceKnowledge
            documents = MultiSpaceKnowledge(project, Remote(project.credentials))
        Server(documents, personal, unavailable).run()
    finally:
        if documents is not None:
            documents.close()
    return 0
