"""User consent for independent Codex reviews, scoped to one checkout and profile."""
import hashlib
import json
import os
from pathlib import Path

from .config import Project, home_directory


def _registration(root, project):
    profile = Path(os.environ.get('CODEX_HOME') or home_directory() / '.codex')
    if not profile.is_absolute():
        raise ValueError('Codex review requires an absolute profile directory')
    key = json.dumps([os.path.normcase(str(path)) for path in (root, profile)], ensure_ascii=True)
    return project.data_home / 'codex-review-projects' / (hashlib.sha256(key.encode()).hexdigest() + '.json')


def _encoded_binding(root, identity, project):
    account = hashlib.sha256(project.credentials.email.strip().casefold().encode()).hexdigest()
    binding = {'project': os.path.normcase(str(root)), 'identity': identity, 'site': project.credentials.base_url,
               'account': account,
               'spaces': sorted((row['key'], row.get('rootPageId')) for row in project.selections)}
    return (json.dumps(binding, ensure_ascii=True, sort_keys=True) + '\n').encode()


def register(root):
    from document_destination import _project_root
    from wiki_platform import atomic_write, ensure_private_directory, review_workspace_identity

    root = root.expanduser().absolute()
    if _project_root(root) != root:
        raise ValueError('Choose the configured consuming project root')
    with review_workspace_identity(root) as identity:
        project = Project.from_project(root, bound_reviewer=True)
        if project is None:
            raise ValueError('Project wiki connection is not configured')
        path = _registration(root, project)
        ensure_private_directory(path.parent)
        atomic_write(path, _encoded_binding(root, identity, project))
        return path


def registered_digest(root, identity, project):
    from wiki_platform import safe_read

    if project is None:
        return None
    expected = _encoded_binding(root, identity, project)
    try:
        stored = safe_read(_registration(root, project), 65536, private=True)
    except (OSError, ValueError):
        return None
    return hashlib.sha256(expected).hexdigest() if stored == expected else None


def reviewer_for(startup, project):
    from document_destination import _project_root
    from wiki_platform import review_workspace_identity

    root = _project_root(startup)
    try:
        path = _registration(root, project)
    except ValueError:
        return None
    if not os.path.lexists(path):
        return None

    def review(target):
        with review_workspace_identity(root) as identity:
            bound = Project.from_project(root, bound_reviewer=True)
            binding = registered_digest(root, identity, bound)
            if binding is None or bound.credentials != project.credentials:
                return {'status': 'BLOCKED_ENV', 'summary': 'Project review registration or account has changed.'}
            from .codex_review import run_review
            return run_review(root, {**target, 'projectIdentity': identity, 'registrationBinding': binding})

    return review
