"""Actual native client/cache coordination with a competing notice reader."""
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from test_remote import NativeState, page_raw
from wiki_atlassian.config import Credentials, Project
from wiki_atlassian.models import RemoteError
from wiki_atlassian.multispace import MultiSpaceKnowledge
from wiki_atlassian.remote import Remote
from wiki_atlassian.replica import Replica


def connected(tmp_path):
    native = NativeState()
    native.pages['20'] = page_raw('20', spaceId='19')
    native.pages['21'] = page_raw('21', '20', spaceId='19', body='<p>Sibling private evidence</p>')
    confirmed = threading.Event()
    def hook(method, path, query, payload):
        if method == 'GET' and path == '/wiki/api/v2/spaces':
            key = query['keys'][0]
            return 200, {}, dict(results=[dict(id='9' if key == 'DOC' else '19', key=key, status='current')])
        if method == 'GET' and path == '/wiki/api/v2/pages/10/versions/2':
            confirmed.set()
            return 401, {}, {}
    native.hook = hook
    credentials = Credentials('https://fixture.invalid', 'fixture@example.invalid', 'synthetic-token')
    project = Project(dict(enabled=True, site=credentials.base_url,
        spaces=[dict(key='DOC', rootPageId='10'), dict(key='OTHER', rootPageId='20')]), credentials, tmp_path / 'cache')
    remote = Remote(credentials, transport=native.transport)
    manager = MultiSpaceKnowledge(project, remote, start_workers=False)
    manager.synchronize('DOC')
    manager.synchronize('OTHER')
    return native, confirmed, project, remote, manager


@pytest.mark.parametrize('scenario', ['write', 'child_recheck', 'removed_page'])
def test_wrapped_401_fences_free_siblings_before_waiting_once(tmp_path, scenario):
    native, confirmed, project, remote, manager = connected(tmp_path)
    selected, sibling = manager.slots['DOC'].replica, manager.slots['OTHER'].replica
    operation, data = 'update_document', dict(space='DOC', document='10', version=1, append='<p>Saved once</p>')
    if scenario == 'child_recheck':
        original = native.hook
        denied_child = False
        def hook(method, path, query, payload):
            nonlocal denied_child
            if method == 'GET' and path == '/wiki/api/v2/pages/11' and query.get('body-format') == ['storage']:
                denied_child = True
                return 403, {}, {}
            if denied_child and method == 'GET' and path == '/wiki/api/v2/pages/10':
                confirmed.set()
                return 401, {}, {}
            return original(method, path, query, payload)
        native.hook = hook
        operation, data = 'get_document', dict(space='DOC', document='11', format='outline')
    elif scenario == 'removed_page':
        original = native.hook
        def hook(method, path, query, payload):
            if method == 'GET' and path == '/wiki/api/v2/pages/10/direct-children':
                return 200, {}, dict(results=[])
            if method == 'GET' and path == '/wiki/api/v2/pages/11':
                if not confirmed.is_set():
                    holder.stdin.write('acquire\n'); holder.stdin.flush()
                    assert holder.stdout.readline().strip() == 'locked'
                confirmed.set()
                return 401, {}, {}
            return original(method, path, query, payload)
        native.hook = hook
    scripts = Path(__file__).resolve().parents[2] / 'plugin/scripts'
    code = ('import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); '
            'from wiki_platform import file_lock; '
            + ('sys.stdin.readline(); ' if scenario == 'removed_page' else '') +
            'lock=file_lock(Path(sys.argv[2])); lock.__enter__(); '
            'print("locked",flush=True); sys.stdin.readline(); lock.__exit__(None,None,None)')
    holder = subprocess.Popen([sys.executable, '-B', '-c', code, str(scripts), str(selected.control / 'notice-access.lock')],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding='utf-8')
    result = []
    started = time.monotonic()
    def run():
        if scenario != 'removed_page':
            result.append(manager.execute(operation, data))
            return
        try:
            manager.synchronize('DOC')
        except RemoteError as error:
            result.append(dict(status='error', code=error.code, details=error.details,
                               authorizationDenied=error.code == 'http_401' or error.details.get('authorizationDenied') is True))
        else:
            result.append(dict(status='unexpected_success'))
    worker = threading.Thread(target=run, daemon=True)
    try:
        if scenario != 'removed_page':
            assert holder.stdout.readline().strip() == 'locked'
        started = time.monotonic()
        worker.start()
        assert confirmed.wait(2)
        deadline = time.monotonic() + .5
        while not (manager.slots['OTHER'].denied and (sibling.control / 'notice-access-denied').exists()) and time.monotonic() < deadline:
            time.sleep(.01)
        assert manager.slots['OTHER'].denied
        assert (sibling.control / 'notice-access-denied').exists()
        assert 'content' not in manager.execute('get_document', dict(document='21'))
        independent = Replica(manager.slots['OTHER'].settings, remote)
        assert independent.get_document(dict(document='21'))['code'] == 'root_scope_denied'
        worker.join(4)
        assert not worker.is_alive() and time.monotonic() - started < 4.5
        assert result[0]['status'] == ('unknown' if scenario == 'write' else 'error')
        assert result[0]['code'] == dict(write='unknown_update', child_recheck='http_403', removed_page='http_401')[scenario]
        assert result[0]['authorizationDenied']
        assert result[0]['details']['scopeFence'] == 'pending'
        assert sum(row[0] == 'PUT' for row in native.calls) == (1 if scenario == 'write' else 0)
    finally:
        holder.stdin.write('\n'); holder.stdin.flush()
        try:
            holder.wait(timeout=3)
        except subprocess.TimeoutExpired:
            holder.terminate(); holder.wait(timeout=3)
        if worker.ident is not None:
            worker.join(5)
    # A later call retries publication; the prior pending result is not durable state.
    assert manager._deny_all() == {}
    assert (selected.control / 'notice-access-denied').exists()
    manager.close()


def test_known_401_revokes_prior_cache_without_initializing_that_space(tmp_path):
    native, confirmed, project, remote, prior = connected(tmp_path)
    sibling_settings = prior.slots['OTHER'].settings
    manager = MultiSpaceKnowledge(project, remote, start_workers=False)
    manager.synchronize('DOC')
    assert manager.slots['OTHER'].replica is None
    result = manager.execute('update_document', dict(space='DOC', document='10', version=1, append='<p>Saved once</p>'))
    assert result['status'] == 'unknown' and result['authorizationDenied']
    assert Replica(sibling_settings, remote).get_document(dict(document='21'))['code'] == 'root_scope_denied'
    assert manager.slots['OTHER'].settings is None and manager.slots['OTHER'].service is None
    assert sum(row[0] == 'PUT' for row in native.calls) == 1
    manager.close(); prior.close()
