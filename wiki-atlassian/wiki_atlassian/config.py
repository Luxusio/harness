"""Immutable selections and authentication from the consuming project."""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from .models import Credentials, RemoteError, compact_json, digest, native_id


AUTH_FIELDS = ('ATLASSIAN_BASE_URL', 'ATLASSIAN_EMAIL', 'ATLASSIAN_API_TOKEN')


def project_credentials(root, environment):
    """Resolve one complete tuple from the exact project file or caller."""
    from wiki_platform import native_executable, private_source, run_bounded, same_file
    path = root / 'mise.local.toml'
    if not os.path.lexists(path):
        return [environment.get(name) for name in AUTH_FIELDS]
    child_env = {key: value for key, value in environment.items()
                 if not key.upper().startswith(('ATLASSIAN_', 'MISE_', '__MISE_'))}
    child_env.update(MISE_YES='0', MISE_AUTO_INSTALL='0')
    try:
        with private_source(path, 65536):
            mise = (native_executable('mise', cwd=root, env=child_env) if os.name == 'nt'
                    else shutil.which('mise', path=child_env.get('PATH')))
            if mise is None:
                raise ValueError('mise is unavailable')
            result = run_bounded([mise, 'env', '--json-extended'], cwd=root, env=child_env,
                                 timeout=10, limit=1048576, capture_stderr=os.name == 'nt')
            if result.returncode:
                raise ValueError('Project authentication evaluation failed')
            data = json.loads(result.stdout.decode('utf-8'))
            values = []
            for name in AUTH_FIELDS:
                entry = data[name]
                value = entry['value']
                if (not same_file(entry['source'], str(path)) or not isinstance(value, str)
                        or not value.strip() or any(ord(c) < 32 for c in value)):
                    raise ValueError('Project authentication source is unavailable')
                values.append(value)
        return values
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        raise ValueError('Project authentication unavailable; configure and trust the project mise.local.toml') from None


def home_directory(environment=None):
    env = os.environ if environment is None else environment
    value = env.get('HOME') if os.name != 'nt' else env.get('USERPROFILE')
    home = Path(value) if value else Path.home()
    if not home.is_absolute() or '..' in home.parts:
        raise ValueError('Invalid user home directory')
    return home


def corpus_path(data_home, credentials, key, root=None, *, page_only=False):
    identity = [credentials.base_url.rstrip('/'), credentials.email, root or '*', key]
    return Path(data_home) / digest([*identity, 'page-only'] if page_only else identity)


class SpaceSettings:
    def __init__(self, config, credentials, corpus_root):
        self._config = copy.deepcopy(config)
        self.credentials = credentials
        self.corpus_root = Path(corpus_root)
        if not self.corpus_root.is_absolute():
            raise ValueError('Absolute corpus path required')
        self.space = self._config['space']
        self.space_id = native_id(self._config['spaceId'])
        self.site = credentials.base_url.rstrip('/')
        self.root_page_id = self._config.get('rootPageId')
        if self.root_page_id is not None:
            native_id(self.root_page_id)
        self.whole_space = self.root_page_id is None
        self.page_only = self._config.get('pageOnly', False)
        if type(self.page_only) is not bool or self.page_only and self.whole_space:
            raise ValueError('Exact-page settings require a root page')
        self.poll_interval_seconds = self._config.get('pollIntervalSeconds', 300)
        self.watched_comment_pages = self._config.get('watchCommentPages', '').split(',') if self._config.get('watchCommentPages') else []
        self.credential_fingerprint = digest([self.site, credentials.email, credentials.api_token])
        self.cursor_key = hmac.new(credentials.api_token.encode('utf-8'), compact_json([
            'wiki-page-cursor-v1', self.site, credentials.email, self.root_page_id,
            self._config.get('privateAccountId')]).encode('utf-8'), hashlib.sha256).digest() if self.page_only else None

    @property
    def config(self):
        return copy.deepcopy(self._config)

    def identity(self):
        return digest([self._config, self.credential_fingerprint]) if self.page_only else digest(self._config)

    def read(self):
        return self.config

    def prepare_corpus(self):
        from wiki_platform import ensure_private_directory
        ensure_private_directory(self.corpus_root)


def personal_settings(selected, credentials, remote, data_home):
    """Resolve only the privately selected page and its actual native space."""
    from document_destination import _origin
    if _origin(selected['site']) != _origin(credentials.base_url):
        raise RemoteError('connection_unavailable', 'Private authentication origin differs from the configured page')
    account = remote.current_user()
    node = remote.scope_node(native_id(selected['page_id']))
    space = remote.space(node.space_id)
    value = dict(enabled=True, site=credentials.base_url, space=space['key'], spaceId=space['id'],
                 rootPageId=node.id, pageOnly=True, privateAccountId=account['accountId'])
    return SpaceSettings(value, credentials, corpus_path(data_home, credentials, space['key'], node.id, page_only=True))


class Project:
    def __init__(self, value, credentials, data_home=None):
        from document_destination import _origin, _validate_config, CONFIG_FIELDS
        if not isinstance(value, dict) or set(value) - CONFIG_FIELDS:
            raise ValueError('Invalid project connection')
        _validate_config(value)
        if value.get('enabled') is not True:
            raise ValueError('Project connection is disabled')
        if (_origin(credentials.base_url) != _origin(value['site'])
                or any(not isinstance(v, str) or not v.strip() or any(ord(c) < 32 for c in v)
                       for v in (credentials.email, credentials.api_token))):
            raise ValueError('Project connection or private authentication unavailable')
        self._config = copy.deepcopy(value)
        self.credentials = Credentials(_origin(credentials.base_url), credentials.email, credentials.api_token)
        self.data_home = Path(data_home) if data_home is not None else home_directory() / '.local/share/wiki-atlassian'
        self.poll_interval_seconds = value.get('pollIntervalSeconds', 300)

    @property
    def selections(self):
        return copy.deepcopy(self._config['spaces'])

    def read(self):
        return copy.deepcopy(self._config)

    def corpus_root(self, key):
        selection = next((row for row in self.selections if row['key'] == key), None)
        if selection is None:
            raise RemoteError('space_not_configured', 'Select a configured space')
        return corpus_path(self.data_home, self.credentials, key, selection.get('rootPageId'))

    def resolve(self, remote, key):
        selection = next((row for row in self.selections if row['key'] == key), None)
        if selection is None:
            raise RemoteError('space_not_configured', 'Select a configured space')
        candidates = [row for row in remote.spaces(key) if row.get('key') == key]
        if len(candidates) != 1:
            raise RemoteError('space_unavailable', 'Selected space is not uniquely accessible')
        value = dict(enabled=True, site=self.credentials.base_url, space=key,
                     spaceId=native_id(candidates[0]['id']), pollIntervalSeconds=self.poll_interval_seconds)
        value.update({k: v for k, v in selection.items() if k != 'key'})
        return SpaceSettings(value, self.credentials, self.corpus_root(key))

    @classmethod
    def from_project(cls, startup, environment=None, *, bound_reviewer=False):
        from document_destination import _project_root, _read_config, _origin, _validate_config
        environment = dict(os.environ if environment is None else environment)
        try:
            root = _project_root(startup)
            if bound_reviewer:
                if Path(root) != Path(startup):
                    raise ValueError('Reviewer registration must identify the consuming project root')
                # A companion has no authority over an account supplied by the host.
                # The normal loader already verifies every mise field's file of origin.
                environment = {key: value for key, value in environment.items() if key.upper() not in AUTH_FIELDS}
            value = _read_config(str(root))
            if value is None:
                return None
            _validate_config(value)
            if value.get('enabled') is False:
                return None
            values = project_credentials(root, environment)
            if any(not isinstance(v, str) or not v.strip() for v in values):
                raise ValueError('Private authentication unavailable')
            credentials = Credentials(_origin(values[0]), values[1], values[2])
            return cls(value, credentials, home_directory(environment) / '.local/share/wiki-atlassian')
        except (OSError, ValueError, TypeError, KeyError, ImportError, RecursionError):
            raise ValueError('Atlassian project connection or authentication unavailable') from None
