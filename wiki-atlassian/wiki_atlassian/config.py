"""Immutable project selections; no credential discovery outside the caller."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import re

from .models import Credentials, RemoteError, digest, native_id


def home_directory(environment=None):
    env = os.environ if environment is None else environment
    value = env.get('HOME') if os.name != 'nt' else env.get('USERPROFILE')
    home = Path(value) if value else Path.home()
    if not home.is_absolute() or '..' in home.parts:
        raise ValueError('Invalid user home directory')
    return home


def corpus_path(data_home, credentials, key, root=None):
    return Path(data_home) / digest([credentials.base_url.rstrip('/'), credentials.email, root or '*', key])


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
        self.poll_interval_seconds = self._config.get('pollIntervalSeconds', 300)
        self.watched_comment_pages = self._config.get('watchCommentPages', '').split(',') if self._config.get('watchCommentPages') else []
        self.credential_fingerprint = digest([self.site, credentials.email, credentials.api_token])

    @property
    def config(self):
        return copy.deepcopy(self._config)

    def identity(self):
        return digest(self._config)

    def read(self):
        return self.config

    def prepare_corpus(self):
        from wiki_platform import ensure_private_directory
        ensure_private_directory(self.corpus_root)


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
    def from_project(cls, startup, environment=None):
        from document_destination import _read_config, _origin
        environment = dict(os.environ if environment is None else environment)
        try:
            value = _read_config(str(startup))
            if value is None or value.get('enabled') is False:
                return None
            values = [environment.get(name) for name in ('ATLASSIAN_BASE_URL', 'ATLASSIAN_EMAIL', 'ATLASSIAN_API_TOKEN')]
            if any(not isinstance(v, str) or not v.strip() for v in values):
                raise ValueError('Private authentication unavailable')
            credentials = Credentials(_origin(values[0]), values[1], values[2])
            return cls(value, credentials, home_directory(environment) / '.local/share/wiki-atlassian')
        except (OSError, ValueError, TypeError, KeyError, ImportError, RecursionError):
            raise ValueError('Atlassian project connection or authentication unavailable') from None
