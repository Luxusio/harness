"""Real filesystem publication, admission, and synthetic native-source contracts."""
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'wiki-atlassian'))
sys.path.insert(0, str(ROOT / 'plugin/scripts'))
from wiki_atlassian.config import SpaceSettings
from wiki_atlassian.models import Credentials, Node, Page, RemoteError, compact_json
from wiki_atlassian.remote import Remote, RootScopeDenial
from wiki_atlassian.replica import Replica, knowledge_relations
from wiki_platform import atomic_write, file_lock
import wiki_platform


class Native:
    """Synthetic provider follows the same typed, scoped contract as Remote."""
    def __init__(self, settings):
        self.settings = settings
        self.pages = {
            '210': Page(Node('210', 'Knowledge', None, 'page', '20', 1), '<h1>Knowledge</h1>'),
            '211': Page(Node('211', 'Operations', '210', 'page', '20', 1), '<h2>Work</h2><p>Shared evidence 😀</p>'),
        }
        self.comments = {}
        self.denied = set()
        self.transient = set()
        self.reads, self.bodies, self.comment_reads = [], [], []
        self.after_body = None
        self.after_root = None
        self.comment_error = {}

    @contextmanager
    def operation(self, **kwargs):
        yield

    def _fail(self, id):
        if id in self.denied:
            error = RemoteError('http_403', 'Synthetic denied')
            if id == self.settings.root_page_id:
                raise RootScopeDenial(self.settings, error)
            raise error
        if id in self.transient:
            raise RemoteError('http_503', 'Synthetic transient')
        if id not in self.pages:
            raise RemoteError('http_404', 'Synthetic missing')

    def require_space(self, settings):
        self.reads.append('space')

    def require_scope(self, settings, id):
        self.reads.append(id)
        self._fail(id)
        node = self.pages[id].node
        if node.space_id != settings.space_id or node.status != 'current':
            error = RemoteError('scope_outside_connection' if node.space_id != settings.space_id else 'scope_not_current', 'Synthetic moved')
            if id == settings.root_page_id:
                raise RootScopeDenial(settings, error)
            raise error
        if not settings.whole_space:
            seen, current = set(), node
            while current.id != settings.root_page_id:
                if current.id in seen or current.parent_id is None:
                    raise RemoteError('scope_outside_connection', 'Synthetic outside')
                seen.add(current.id)
                self._fail(current.parent_id)
                current = self.pages[current.parent_id].node
            if self.after_root is not None:
                self.after_root(id)
        return node

    def scope_node(self, id, type='page'):
        self._fail(id)
        node = self.pages[id].node
        if node.type != type:
            raise RemoteError('http_404', 'Synthetic type mismatch')
        return node

    def inventory(self, settings):
        self.require_space(settings) if settings.whole_space else self.require_scope(settings, settings.root_page_id)
        result = []
        for id in self.pages:
            if id in self.denied:
                continue
            try:
                result.append(self.require_scope(settings, id))
            except RootScopeDenial:
                raise
            except RemoteError:
                continue
        self.require_space(settings) if settings.whole_space else self.require_scope(settings, settings.root_page_id)
        return result

    def page(self, id, expected_node=None, scope=None, fresh_storage=False):
        node = self.require_scope(scope or self.settings, id)
        self.bodies.append((id, fresh_storage))
        page = self.pages[id]
        if self.after_body is not None:
            self.after_body(id)
        final = self.require_scope(scope or self.settings, id)
        if node != final or (expected_node is not None and node != expected_node):
            raise RemoteError('scope_metadata_changed', 'Synthetic changed')
        return page

    def inventory_page(self, settings, node, fresh_storage=False):
        return self.page(node.id, expected_node=node, scope=settings, fresh_storage=fresh_storage)

    def verify_inventory(self, settings, nodes):
        for node in nodes:
            if self.scope_node(node.id, node.type) != node:
                raise RemoteError('scope_metadata_changed', 'Synthetic inventory changed')

    def comment_inventory(self, target, expected_node=None, scope=None):
        self.comment_reads.append(target)
        if target in self.comment_error:
            raise self.comment_error[target]
        return dict(provider='confluence', target=target, comments=self.comments.get(target, []), complete=True)


def test_cold_native_root_reuses_observations_within_the_existing_budget(tmp_path):
    from test_remote import NativeState, page_raw
    for count in (30, 50):
        state = NativeState()
        state.pages = {str(10+i): page_raw(str(10+i), '10' if i else None) for i in range(count)}
        settings = SpaceSettings(dict(site='https://fixture.invalid', space='DOC', spaceId='9', rootPageId='10'),
            Credentials('https://fixture.invalid', 'fixture@example.invalid', 'synthetic'), tmp_path / str(count))
        remote = Remote(settings.credentials, state.transport)
        replica = Replica(settings, remote)
        try:
            result = replica.synchronize()
            assert result['pages'] == count and len(state.calls) <= 512
            assert replica.get_document(dict(document=str(9+count)))['content'].endswith('\n\nOriginal')
            manifest = json.loads((replica.control / 'manifest.json').read_text())
            assert manifest['inventoryComplete'] and len(manifest['pages']) == count
        finally:
            replica.close()


def test_native_sweep_reverifies_earlier_nodes_before_publishing_any_body(tmp_path):
    from test_remote import NativeState, page_raw
    state = NativeState()
    state.pages['12'] = page_raw('12', '10')
    def hook(method, path, query, payload):
        if method == 'GET' and path == '/wiki/api/v2/pages/12' and query.get('body-format') == ['storage']:
            state.pages['11']['parentId'] = '99'
    state.hook = hook
    settings = SpaceSettings(dict(site='https://fixture.invalid', space='DOC', spaceId='9', rootPageId='10'),
        Credentials('https://fixture.invalid', 'fixture@example.invalid', 'synthetic'), tmp_path / 'cache')
    replica = Replica(settings, Remote(settings.credentials, state.transport))
    try:
        try:
            replica.synchronize()
        except RemoteError as error:
            assert error.code == 'scope_metadata_changed'
        else:
            raise AssertionError('A moved earlier node was published from an old observation')
        assert not (replica.control / 'manifest.json').exists()
    finally:
        replica.close()


class ReplicaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'corpus'
        self.settings = self.setting()
        self.remote = Native(self.settings)
        self.replica = Replica(self.settings, self.remote)
        self.addCleanup(self.replica.close)

    def setting(self, token='fixture', root='210', watch=None):
        config = dict(enabled=True, site='https://example.test', space='COMMON', spaceId='20', pollIntervalSeconds=300)
        if root is not None:
            config['rootPageId'] = root
        if watch:
            config['watchCommentPages'] = watch
        return SpaceSettings(config, Credentials('https://example.test', 'fixture@example.test', token), self.root)

    def metadata(self, name):
        return json.loads((self.replica.control / name).read_text('utf-8'))

    def write_metadata(self, name, value):
        atomic_write(self.replica.control / name, compact_json(value).encode('utf-8'))

    def expire(self):
        state = self.metadata('state.json')
        state['bodyRefreshedAt'] = 0
        self.write_metadata('state.json', state)

    def change(self, id, body=None, **node):
        current = self.remote.pages[id]
        self.remote.pages[id] = Page(replace(current.node, **node), current.body if body is None else body)
        return current, self.remote.pages[id]

    @contextmanager
    def competing_notice_lock(self, seconds):
        script = ('import sys,time; sys.path.insert(0,sys.argv[1]); from wiki_platform import file_lock\n'
                  'with file_lock(sys.argv[2]):\n print("locked",flush=True); time.sleep(float(sys.argv[3]))\n')
        process = subprocess.Popen([sys.executable, '-B', '-c', script, str(Path(wiki_platform.__file__).parent),
                                    str(self.replica.control / 'notice-access.lock'), str(seconds)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ready, output = threading.Event(), []
        def read_ready():
            output.append(process.stdout.readline().strip())
            ready.set()
        reader = threading.Thread(target=read_ready, daemon=True)
        reader.start()
        try:
            self.assertTrue(ready.wait(5), 'Competing lock holder did not start')
            self.assertEqual(['locked'], output)
            yield process
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
            reader.join(timeout=1)
            process.stdout.close()
            process.stderr.close()

    def test_cold_pending_failed_status_sanitized_then_success_and_shared_offline_read(self):
        self.assertEqual('pending', self.replica.status()['status'])
        self.replica.sync_failed('secret token fixture')
        self.assertEqual('sync_failed', self.replica.status()['code'])
        self.replica.synchronize()
        self.assertEqual('ready', self.replica.status()['status'])
        self.remote.reads.clear()
        other = Replica(self.settings, self.remote)
        self.assertIn('Shared evidence 😀', other.get_document(dict(document='211'))['content'])
        self.assertEqual('local_replica', other.search(dict(query='Shared'))['freshness']['source'])
        self.assertEqual([], self.remote.reads)

    def test_actual_remote_native_protocol_drives_shared_replica_and_confirmed_denial(self):
        calls, denied = [], set()
        pages = dict(self.remote.pages)
        def transport(method, target, headers, data, timeout):
            uri = urlsplit(target)
            query = parse_qs(uri.query)
            calls.append((method, uri.path, query))
            self.assertEqual('GET', method)
            self.assertIsNone(data)
            self.assertTrue(headers['Authorization'].startswith('Basic '))
            self.assertGreater(timeout, 0)
            match = re.fullmatch(r'/wiki/api/v2/(pages|folders)/([1-9][0-9]*)(/direct-children)?', uri.path)
            if match is None:
                return 404, {}, b'{}'
            kind, id, children = match.groups()
            if id in denied:
                return 403, {}, b'{}'
            if id not in pages or kind != ('folders' if pages[id].node.type == 'folder' else 'pages'):
                return 404, {}, b'{}'
            if children:
                value = dict(results=[dict(id=p.node.id, type=p.node.type) for p in pages.values()
                                      if p.node.parent_id == id and p.node.id not in denied])
            else:
                page = pages[id]
                node = page.node
                value = dict(id=node.id, title=node.title, type=node.type, spaceId=node.space_id,
                             status=node.status, parentId=node.parent_id, version=dict(number=node.version))
                if query.get('body-format') == ['storage']:
                    value['body'] = dict(storage=dict(value=page.body, representation='storage'))
            return 200, {}, compact_json(value).encode('utf-8')
        remote = Remote(self.settings.credentials, transport=transport)
        replica = Replica(self.settings, remote)
        replica.synchronize()
        count = len(calls)
        hit = replica.search(dict(query='Shared'))['results'][0]
        self.assertEqual(hit['excerpt'], replica.get_document(hit['read'])['content'])
        self.assertEqual(count, len(calls))
        self.expire()
        pages['211'] = Page(pages['211'].node, '<p>Native authoritative changed marker</p>')
        replica.synchronize()
        self.assertIn('Native authoritative changed marker', replica.get_document(dict(document='211'))['content'])
        denied.add('211')
        replica.synchronize()
        self.assertEqual('document_unavailable', replica.get_document(dict(document='211'))['code'])
        self.assertFalse((replica.control / 'notice-access-denied').exists())
        denied.add('210')
        with self.assertRaises(RootScopeDenial):
            replica.synchronize()
        self.assertTrue((replica.control / 'notice-access-denied').exists())

    def test_actual_remote_omitted_page_recheck_propagates_401_and_retains_partial_403_404(self):
        pages = dict(self.remote.pages)
        for status in (401, 403, 404, 503):
            with self.subTest(status=status):
                settings = SpaceSettings(self.settings.config, self.settings.credentials, self.root / str(status))
                omitted, calls, fences = False, [], []
                def transport(method, target, headers, data, timeout):
                    uri, query = urlsplit(target), parse_qs(urlsplit(target).query)
                    calls.append(uri.path)
                    match = re.fullmatch(r'/wiki/api/v2/pages/([1-9][0-9]*)(/direct-children)?', uri.path)
                    if method != 'GET' or match is None:
                        return 404, {}, b'{}'
                    id, children = match.groups()
                    if omitted and id == '211':
                        return status, {}, b'{}'
                    page = pages.get(id)
                    if page is None:
                        return 404, {}, b'{}'
                    if children:
                        value = dict(results=[] if omitted else [dict(id=p.node.id, type=p.node.type)
                                     for p in pages.values() if p.node.parent_id == id])
                    else:
                        node = page.node
                        value = dict(id=id, title=node.title, type=node.type, spaceId=node.space_id,
                                     status=node.status, parentId=node.parent_id, version=dict(number=node.version))
                        if query.get('body-format') == ['storage']:
                            value['body'] = dict(storage=dict(value=page.body, representation='storage'))
                    return 200, {}, compact_json(value).encode('utf-8')
                replica = Replica(settings, Remote(settings.credentials, transport=transport))
                self.addCleanup(replica.close)
                replica.synchronize()
                def global_fence():
                    fences.append(True)
                    replica.deny(timeout=0)
                    return {}
                replica.on_authorization_denied = global_fence
                omitted = True
                calls.clear()
                if status == 401:
                    with self.assertRaises(RemoteError) as caught:
                        replica.synchronize()
                    self.assertEqual('http_401', caught.exception.code)
                    self.assertTrue(caught.exception.details['authorizationDenied'])
                    self.assertEqual([True], fences)
                    self.assertTrue((replica.control / 'notice-access-denied').exists())
                    self.assertEqual('root_scope_denied', replica.get_document(dict(document='210'))['code'])
                else:
                    replica.synchronize()
                    self.assertEqual([], fences)
                    self.assertFalse((replica.control / 'notice-access-denied').exists())
                    self.assertEqual('document_unavailable', replica.get_document(dict(document='211'))['code'])
                    self.assertIn('content', replica.get_document(dict(document='210')))
                self.assertEqual(1, calls.count('/wiki/api/v2/pages/211'))

    def test_authoritative_same_version_sweep_is_shared_and_repairs_survive_restart(self):
        self.replica.synchronize()
        self.remote.bodies.clear()
        self.change('211', '<p>Changed without native revision</p>')
        other = Replica(self.settings, self.remote)
        other.synchronize()
        self.assertEqual([], self.remote.bodies)
        self.expire()
        other.synchronize()
        self.assertIn(('211', True), self.remote.bodies)
        self.assertIn('Changed without native revision', other.get_document(dict(document='211'))['content'])
        self.assertEqual([], self.metadata('manifest.json')['explicitPages'])

    def test_requested_repair_bypasses_checkpoint_for_one_page_without_advancing_sweep(self):
        self.replica.synchronize()
        before_stamp = self.metadata('state.json')['bodyRefreshedAt']
        self.change('211', '<p>Same version inline marker</p>')
        signals = []
        self.replica.on_repair = lambda: signals.append(True)
        self.replica.request_repair('211')
        self.remote.bodies.clear()
        self.replica.synchronize(authoritative_bodies=False)
        self.assertEqual([( '211', True)], self.remote.bodies)
        self.assertEqual(before_stamp, self.metadata('state.json')['bodyRefreshedAt'])
        self.assertEqual([True], signals)
        self.assertFalse(self.replica.pending_repair)

    def test_malformed_negative_and_future_sweep_stamps_force_authoritative_body(self):
        self.replica.synchronize()
        for stamp in [-1, 9223372036854775807, '0', True, {}, []]:
            with self.subTest(stamp=stamp):
                state = self.metadata('state.json')
                state['bodyRefreshedAt'] = stamp
                self.write_metadata('state.json', state)
                self.remote.bodies.clear()
                self.replica.synchronize()
                self.assertIn(('211', True), self.remote.bodies)

    def test_token_rotation_requires_fresh_scope_reuses_bodies_and_does_not_revoke_active_credential(self):
        self.replica.synchronize()
        self.expire()
        rotated_settings = self.setting('rotated')
        rotated = Replica(rotated_settings, self.remote)
        self.assertEqual('credential_reauthorization_pending', rotated.get_document(dict(document='211'))['code'])
        self.remote.denied.add('210')
        with self.assertRaises(RemoteError):
            rotated.synchronize()
        self.assertFalse((self.replica.control / 'notice-access-denied').exists())
        self.assertEqual(self.settings.credential_fingerprint, self.metadata('state.json')['credentialBinding'])
        self.remote.denied.clear()
        self.remote.bodies.clear()
        rotated.synchronize()
        self.assertEqual([], self.remote.bodies)
        self.assertEqual(0, self.metadata('state.json')['bodyRefreshedAt'])
        self.replica.deny()
        self.assertFalse((self.replica.control / 'notice-access-denied').exists())
        self.assertEqual('credential_reauthorization_pending', self.replica.get_document(dict(document='211'))['code'])
        rotated.synchronize()
        self.assertIn(('211', True), self.remote.bodies)

    def test_missing_child_retains_unavailable_record_but_removes_access_and_native_archival_removes_row(self):
        self.replica.synchronize()
        old = self.metadata('manifest.json')['pages']['211']
        self.remote.denied.add('211')
        result = self.replica.synchronize()
        self.assertEqual(['211'], result['unavailable'])
        self.assertEqual(old, self.metadata('manifest.json')['pages']['211'])
        self.assertEqual('document_unavailable', self.replica.get_document(dict(document='211'))['code'])
        self.assertEqual([], self.replica.search(dict(query='Shared'))['results'])
        self.remote.denied.clear()
        self.change('211', status='archived')
        self.replica.synchronize()
        self.assertNotIn('211', self.metadata('manifest.json')['pages'])

    def test_root_denial_fences_reads_and_feed_until_complete_authorized_sync(self):
        self.replica.synchronize()
        feed = (self.replica.control / 'changes.json').read_bytes()
        self.remote.denied.add('210')
        with self.assertRaises(RootScopeDenial):
            self.replica.synchronize()
        self.assertEqual(feed, (self.replica.control / 'changes.json').read_bytes())
        self.assertEqual('root_scope_denied', self.replica.get_document(dict(document='211'))['code'])
        self.remote.denied.clear()
        self.remote.transient.add('210')
        with self.assertRaises(RemoteError):
            self.replica.synchronize()
        self.assertTrue((self.replica.control / 'notice-access-denied').exists())
        self.remote.transient.clear()
        self.replica.synchronize()
        self.assertFalse((self.replica.control / 'notice-access-denied').exists())
        self.assertEqual(feed, (self.replica.control / 'changes.json').read_bytes())

    def test_late_denial_marker_survives_inventory_that_started_before_it(self):
        self.replica.synchronize()
        self.replica.deny()
        earlier = (self.replica.control / 'notice-access-denied').read_bytes()
        def deny_during_root(id):
            self.remote.after_root = None
            self.replica.deny()
        self.remote.after_root = deny_during_root
        self.replica.synchronize()
        newer = (self.replica.control / 'notice-access-denied').read_bytes()
        self.assertNotEqual(earlier, newer)
        self.assertEqual('root_scope_denied', self.replica.get_document(dict(document='211'))['code'])
        self.replica.synchronize()
        self.assertFalse((self.replica.control / 'notice-access-denied').exists())

    def test_child_denial_does_not_fence_scope_but_wrapped_confirmed_root_denial_does(self):
        self.replica.synchronize()
        self.replica.handle_denial(RemoteError('http_403', 'Child denied'))
        self.assertFalse((self.replica.control / 'notice-access-denied').exists())
        wrapper = RemoteError('unknown_create_comment', 'Outcome unknown', outcome_unknown=True)
        wrapper.__cause__ = RootScopeDenial(self.settings, RemoteError('http_403', 'Root denied'))
        self.replica.handle_denial(wrapper)
        self.assertTrue((self.replica.control / 'notice-access-denied').exists())

    def test_confirmed_denial_waits_for_real_competing_lease_and_preserves_primary_error(self):
        self.replica.synchronize()
        self.remote.denied.add('210')
        with self.competing_notice_lock(.3):
            started = time.monotonic()
            result = self.replica.relations(dict(document='211'))
            self.assertGreater(time.monotonic()-started, .15)
            self.assertEqual('http_403', result['code'])
        self.assertTrue((self.replica.control / 'notice-access-denied').exists())
        self.assertEqual('root_scope_denied', Replica(self.settings, self.remote).get_document(dict(document='211'))['code'])

    def test_pending_denial_is_bounded_memory_fenced_and_retried_without_new_ledger(self):
        self.replica.synchronize()
        wakes = []
        self.replica.on_repair = lambda: wakes.append(True)
        with self.competing_notice_lock(1):
            started = time.monotonic()
            with self.assertRaises(RemoteError) as caught:
                self.replica.deny(timeout=.05)
            self.assertLess(time.monotonic()-started, .3)
            self.assertEqual('replica_denial_pending', caught.exception.code)
            self.assertEqual('root_scope_denied', self.replica.get_document(dict(document='211'))['code'])
            self.assertEqual('replica_denial_pending', self.replica.status()['code'])
            self.assertFalse((self.replica.control / 'notice-access-denied').exists())
            self.assertEqual([], wakes, 'Background denial must not create a wake/HTTP loop')
        self.replica.deny()
        self.assertTrue((self.replica.control / 'notice-access-denied').exists())
        self.replica.synchronize()
        self.assertIn('content', self.replica.get_document(dict(document='211')))

    def test_pending_fence_preserves_primary_graph_denial_and_background_401(self):
        self.replica.synchronize()
        original_deny = self.replica.deny
        self.replica.deny = lambda timeout=3: original_deny(timeout=min(timeout, .05))
        self.remote.denied.add('210')
        with self.competing_notice_lock(1):
            result = self.replica.relations(dict(document='211'))
            self.assertEqual('http_403', result['code'])
            self.assertEqual('pending', result['details']['scopeFence'])
            self.assertEqual('replica_denial_pending', result['details']['scopeFenceCode'])
        original_fail = self.remote._fail
        lease = None
        def unauthorized_root(id):
            nonlocal lease
            if id == '210':
                # The first metadata notice lease is already released when
                # native authorization discovers the new denial.
                if lease is None:
                    lease = self.competing_notice_lock(1)
                    lease.__enter__()
                raise RootScopeDenial(self.settings, RemoteError('http_401', 'Synthetic revoked token'))
            return original_fail(id)
        self.remote._fail = unauthorized_root
        try:
            with self.assertRaises(RootScopeDenial) as caught:
                self.replica.synchronize()
            self.assertEqual('http_401', caught.exception.code)
            self.assertEqual('pending', caught.exception.details['scopeFence'])
        finally:
            if lease is not None:
                lease.__exit__(None, None, None)

    def test_authorization_denial_hook_runs_before_local_lease_wait_and_only_for_401(self):
        self.replica.synchronize()
        calls = []
        def global_fence():
            calls.append(True)
            self.replica.deny(timeout=0)
            return {}
        self.replica.on_authorization_denied = global_fence
        wrapped = RemoteError('unknown_update', 'Synthetic unknown write', outcome_unknown=True)
        wrapped.__cause__ = RootScopeDenial(self.settings, RemoteError('http_401', 'Synthetic expired token'))
        self.replica.handle_denial(wrapped)
        self.assertEqual([True], calls)
        self.assertTrue((self.replica.control / 'notice-access-denied').exists())
        self.assertEqual('root_scope_denied', self.replica.get_document(dict(document='211'))['code'])
        self.replica.synchronize()
        self.replica.handle_denial(RootScopeDenial(self.settings, RemoteError('http_403', 'Synthetic root loss')))
        self.assertEqual([True], calls, 'A per-space 403 must not revoke sibling spaces')

    def test_authorization_denial_hook_failure_is_explicit_and_memory_fenced(self):
        self.replica.synchronize()
        def failed_coordinator():
            raise OSError('Synthetic coordinator publication failure')
        self.replica.on_authorization_denied = failed_coordinator
        with self.assertRaises(RemoteError) as caught:
            self.replica.handle_denial(RemoteError('http_401', 'Synthetic authentication loss'))
        self.assertEqual('replica_denial_pending', caught.exception.code)
        self.assertEqual('root_scope_denied', self.replica.get_document(dict(document='211'))['code'])
        self.assertEqual('replica_denial_pending', self.replica.status()['code'])

    def test_child_denial_root_recheck_routes_new_401_to_global_hook(self):
        self.replica.synchronize()
        calls = []
        original_root = self.replica._root_access
        def expired_root():
            raise RootScopeDenial(self.settings, RemoteError('http_401', 'Synthetic expired credential'))
        def global_fence():
            calls.append(True)
            self.replica.deny(timeout=0)
            return {}
        self.replica._root_access = expired_root
        self.replica.on_authorization_denied = global_fence
        error = RemoteError('http_403', 'Synthetic child restriction')
        self.replica.handle_denial(error)
        self.assertEqual([True], calls)
        self.assertEqual('http_403', error.code)
        self.assertTrue(error.details['authorizationDenied'])
        self.assertTrue(self.replica._error(error)['authorizationDenied'])
        self.assertTrue((self.replica.control / 'notice-access-denied').exists())
        self.assertEqual('root_scope_denied', self.replica.get_document(dict(document='211'))['code'])
        self.replica._root_access = original_root

    def test_child_denial_root_recheck_retains_401_provenance_when_fence_pending(self):
        self.replica.synchronize()
        def expired_root():
            raise RootScopeDenial(self.settings, RemoteError('http_401', 'Synthetic expired credential'))
        self.replica._root_access = expired_root
        self.replica.on_authorization_denied = lambda: {'DOC': 'replica_denial_pending'}
        error = RemoteError('http_404', 'Synthetic child absence')
        with self.assertRaises(RemoteError) as caught:
            self.replica.handle_denial(error, timeout=.01)
        self.assertEqual('replica_denial_pending', caught.exception.code)
        self.assertEqual('http_404', error.code)
        self.assertTrue(error.details['authorizationDenied'])
        self.assertEqual('root_scope_denied', self.replica.get_document(dict(document='211'))['code'])

    def test_late_old_credential_comment_observation_cannot_commit_after_rotation(self):
        self.replica.synchronize()
        rotated = Replica(self.setting('rotated'), self.remote)
        original = self.remote.comment_inventory
        def rotate_during_observation(*args, **kwargs):
            rotated.synchronize()
            return original(*args, **kwargs)
        self.remote.comment_inventory = rotate_during_observation
        self.replica.poll_comments(['211'], 0)
        current = self.metadata('manifest.json')
        self.assertEqual(rotated.settings.credential_fingerprint, current['credentialBinding'])
        self.assertNotIn('211', current.get('wikiCommentWatch', {}))

    def test_transient_sync_failure_keeps_snapshot_readable_and_reports_staleness(self):
        self.replica.synchronize()
        self.remote.transient.add('210')
        with self.assertRaises(RemoteError):
            self.replica.synchronize()
        status = self.replica.status()
        self.assertTrue(status['ready'])
        self.assertEqual('failed', status['status'])
        self.assertIn('http_503', status['freshness']['syncError'])
        self.assertIn('content', self.replica.get_document(dict(document='211')))

    def test_interrupted_feed_publication_blocks_reads_and_replays_original_sequence(self):
        self.replica.synchronize()
        before, after = self.change('211', '<p>Same-version verified inline body</p>')
        feed = self.replica.control / 'changes.json'
        feed.unlink()
        feed.mkdir()
        with self.assertRaises((OSError, ValueError)):
            self.replica.publish_verified(after, before)
        self.assertTrue((self.replica.control / 'transaction.json').exists())
        self.assertEqual('replica_recovery_pending', self.replica.get_document(dict(document='211'))['code'])
        staged_feed = self.metadata('manifest.json')['wikiNotices']
        self.assertEqual(1, staged_feed['head'])
        feed.rmdir()
        self.replica.synchronize()
        self.assertFalse((self.replica.control / 'transaction.json').exists())
        self.assertEqual(staged_feed, self.metadata('changes.json'))
        self.assertIn('Same-version verified inline body', self.replica.get_document(dict(document='211'))['content'])

    def test_rotated_inventory_recovers_retired_partial_journal_without_importing_its_binding(self):
        self.replica.synchronize()
        before, after = self.change('211', '<p>Retired verified body</p>')
        feed = self.replica.control / 'changes.json'
        feed.unlink()
        feed.mkdir()
        with self.assertRaises((OSError, ValueError)):
            self.replica.publish_verified(after, before)
        journal = self.metadata('transaction.json')
        staged = self.replica.control / journal['stage'] / 'manifest.json'
        manifest = json.loads(staged.read_text('utf-8'))
        rotated = Replica(self.setting('rotated'), self.remote)
        cross_bound = dict(manifest, credentialBinding=rotated.settings.credential_fingerprint)
        atomic_write(staged, compact_json(cross_bound).encode('utf-8'))
        feed.rmdir()
        with self.assertRaises(RemoteError) as error:
            rotated.synchronize()
        self.assertEqual('invalid_mirror_snapshot', error.exception.code)
        self.assertEqual(self.settings.credential_fingerprint, self.metadata('manifest.json')['credentialBinding'])
        atomic_write(staged, compact_json(manifest).encode('utf-8'))
        original_inventory = self.remote.inventory
        def inventory_after_recovery(settings):
            self.assertFalse((self.replica.control / 'transaction.json').exists())
            self.assertEqual(self.settings.credential_fingerprint, self.metadata('manifest.json')['credentialBinding'])
            return original_inventory(settings)
        self.remote.inventory = inventory_after_recovery
        rotated.synchronize()
        self.assertEqual(rotated.settings.credential_fingerprint, self.metadata('manifest.json')['credentialBinding'])

    def test_unknown_settings_bytes_remain_unchanged_during_sync_and_repair(self):
        self.root.mkdir(mode=0o700)
        original = b'{"unknown":"preserve exact bytes", "future": [1, 2]}\n'
        atomic_write(self.root / 'settings.json', original)
        self.replica.synchronize()
        self.replica.request_repair('211')
        self.replica.synchronize(authoritative_bodies=False)
        self.assertEqual(original, (self.root / 'settings.json').read_bytes())

    def test_oversized_candidate_is_refused_before_journal_and_retains_readable_generation(self):
        from wiki_atlassian.replica import MANIFEST_LIMIT
        body = '<h2>지식 연결</h2>' + ''.join(
            '<p>근거: <ac:link><ri:page ri:content-id="211" /></ac:link> — '+('x'*990)+f'{i:010d}</p>' for i in range(100))
        for number in range(500, 660):
            id = str(number)
            self.remote.pages[id] = Page(Node(id, 'Bounded '+id, '210', 'page', '20', 1), body)
        self.replica.synchronize()
        baseline = (self.replica.control / 'manifest.json').read_bytes()
        self.assertLessEqual(len(baseline), MANIFEST_LIMIT)
        self.assertLess(MANIFEST_LIMIT-len(baseline), 110000)
        candidate = Page(Node('999', 'Additional', '210', 'page', '20', 1), body)
        with self.assertRaises(RemoteError) as caught:
            self.replica.publish_verified(candidate)
        self.assertEqual('replica_metadata_limit', caught.exception.code)
        self.assertFalse((self.replica.control / 'transaction.json').exists())
        self.assertEqual(baseline, (self.replica.control / 'manifest.json').read_bytes())
        self.assertIn('content', self.replica.get_document(dict(document='211')))

    def test_large_malformed_sweep_value_does_not_strand_recovery_state(self):
        self.replica.synchronize()
        self.write_metadata('state.json', dict(bodyRefreshedAt='x'*65300))
        self.replica.synchronize()
        self.assertFalse((self.replica.control / 'transaction.json').exists())
        self.assertIs(type(self.metadata('state.json')['bodyRefreshedAt']), int)

    def test_storage_tamper_rejects_read_and_sync_retrieves_untampered_native_body(self):
        self.replica.synchronize()
        row = self.metadata('manifest.json')['pages']['211']
        atomic_write(self.replica.base / row['storage'], b'<p>forged cache</p>')
        self.assertEqual('replica_unavailable', self.replica.get_document(dict(document='211', format='storage'))['code'])
        self.remote.bodies.clear()
        self.replica.synchronize(authoritative_bodies=False)
        self.assertEqual([('211', False)], self.remote.bodies)
        self.assertIn('Shared evidence', self.replica.get_document(dict(document='211', format='storage'))['content'])

    @unittest.skipIf(os.name == 'nt', 'POSIX legacy mode compatibility; native DACL tested by platform lane')
    def test_legacy_readable_metadata_and_nonwritable_directory_modes_are_reused_without_chmod(self):
        self.replica.synchronize()
        row = self.metadata('manifest.json')['pages']['211']
        markdown = self.replica.base / row['path']
        markdown.chmod(0o644)
        markdown.parent.chmod(0o755)
        storage = self.replica.base / row['storage']
        storage.chmod(0o644)
        storage.parent.chmod(0o755)
        self.assertIn('Shared evidence', self.replica.get_document(dict(document='211'))['content'])
        before, after = self.change('211', body='<p>Native verified next version</p>', version=2)
        self.replica.publish_verified(after, before)
        self.assertIn('Native verified next version', self.replica.get_document(dict(document='211'))['content'])
        self.assertEqual(0o755, markdown.parent.stat().st_mode & 0o777)
        self.assertEqual(0o755, storage.parent.stat().st_mode & 0o777)

    def test_native_version_history_falls_back_but_snapshot_never_does(self):
        self.replica.synchronize()
        self.assertIsNone(self.replica.get_document(dict(document='211', version=999)))
        self.assertIsNone(self.replica.get_document(dict(document='211', format='outline')))
        self.assertIsNone(self.replica.get_document(dict(document='211', version=1, section='1')))
        self.assertEqual('invalid_snapshot', self.replica.get_document(dict(document='211', snapshot='x', version=1))['code'])

    def test_filename_allocation_reserves_casefold_file_subtree_and_windows_devices(self):
        for id, title in [('212', 'A'), ('213', 'a'), ('214', 'A.md'), ('215', 'CON'), ('216', 'NUL.txt')]:
            self.remote.pages[id] = Page(Node(id, title, '210', 'page', '20', 1), '<p>file</p>')
        self.replica.synchronize()
        paths = [r['path'].casefold() for r in self.metadata('manifest.json')['pages'].values()]
        self.assertEqual(len(paths), len(set(paths)))
        self.assertFalse(set(paths) & {p[:-3] for p in paths})
        self.assertTrue(any('_con.md' in p for p in paths))
        self.assertTrue(any('_nul.txt.md' in p for p in paths))

    def test_nested_folders_scope_and_verified_rename_relocate_descendant_projection(self):
        self.remote.pages['220'] = Page(Node('220', 'Folder', '211', 'folder', '20', 1), '')
        self.remote.pages['221'] = Page(Node('221', 'Leaf', '220', 'page', '20', 1), '<p>Scoped leaf marker</p>')
        self.replica.synchronize()
        self.assertEqual(['221'], [r['id'] for r in self.replica.search(dict(query='marker', root='220'))['results']])
        old = self.metadata('manifest.json')['pages']['221']['path']
        before, after = self.change('211', title='Renamed', version=2)
        self.replica.publish_verified(after, before)
        new = self.metadata('manifest.json')['pages']['221']['path']
        self.assertNotEqual(old, new)
        self.assertFalse((self.replica.base / old).exists())
        self.assertTrue((self.replica.base / new).is_file())
        self.assertIn('Scoped leaf', self.replica.get_document(dict(document='221'))['content'])

    def exercise_file_directory_title_transition(self, publish):
        self.remote.pages['212'] = Page(Node('212', 'Child', '211', 'page', '20', 1), '<p>Retained descendant</p>')
        self.replica.synchronize()
        for version, title in enumerate(['Operations.md', 'Operations', 'Operations.md'], 2):
            before, after = self.change('211', title=title, version=version)
            if publish:
                self.replica.publish_verified(after, before)
            else:
                self.replica.synchronize()
            rows = self.metadata('manifest.json')['pages']
            self.assertTrue(rows['212']['path'].startswith(rows['211']['path'][:-3]+'/'))
            self.assertIn('Retained descendant', self.replica.get_document(dict(document='212'))['content'])
            self.assertFalse((self.replica.control / 'transaction.json').exists())

    def test_synchronization_replaces_obsolete_owned_files_and_directories_for_title_transitions(self):
        self.exercise_file_directory_title_transition(False)

    def test_verified_publication_replaces_obsolete_owned_files_and_directories_for_title_transitions(self):
        self.exercise_file_directory_title_transition(True)

    def test_topology_transaction_replays_after_new_markdown_precedes_manifest(self):
        from unittest.mock import patch
        import wiki_atlassian.replica as implementation
        self.remote.pages['212'] = Page(Node('212', 'Child', '211', 'page', '20', 1), '<p>Retained descendant</p>')
        self.replica.synchronize()
        before, after = self.change('211', title='Operations.md', version=2)
        original = implementation.atomic_write
        def interrupt_manifest(path, data):
            if Path(path) == self.replica.control / 'manifest.json':
                raise OSError('Synthetic interruption after Markdown publication')
            return original(path, data)
        with patch.object(implementation, 'atomic_write', side_effect=interrupt_manifest), self.assertRaises(OSError):
            self.replica.publish_verified(after, before)
        self.assertTrue((self.replica.control / 'transaction.json').exists())
        self.assertTrue((self.replica.base / 'pages/Knowledge/Operations.md').is_dir())
        self.assertEqual('replica_recovery_pending', self.replica.get_document(dict(document='212'))['code'])
        self.replica.synchronize()
        self.assertFalse((self.replica.control / 'transaction.json').exists())
        self.assertIn('Retained descendant', self.replica.get_document(dict(document='212'))['content'])

    def test_expanded_markdown_generation_is_refused_before_any_journal(self):
        body = ''.join('<ac:image><ri:attachment ri:filename="'+('a'*4000)+str(i)+'" /></ac:image>' for i in range(980))
        self.assertLess(len(body.encode('utf-8')), 4*1024*1024)
        for number in range(211, 219):
            id = str(number)
            self.remote.pages[id] = Page(Node(id, 'Expanded '+id, '210', 'page', '20', 1), body)
        self.replica.synchronize()
        baseline = (self.replica.control / 'manifest.json').read_bytes()
        rows = self.metadata('manifest.json')['pages']
        projected = sum((self.replica.base / row['path']).stat().st_size for row in rows.values())
        self.assertLess(projected, 64*1024*1024)
        self.assertGreater(projected, 56*1024*1024)
        page = Page(Node('999', 'Ninth expanded page', '210', 'page', '20', 1), body)
        with self.assertRaises(RemoteError) as caught:
            self.replica.publish_verified(page)
        self.assertEqual('replica_body_limit', caught.exception.code)
        self.assertFalse((self.replica.control / 'transaction.json').exists())
        self.assertEqual(baseline, (self.replica.control / 'manifest.json').read_bytes())
        self.assertIn('content', self.replica.get_document(dict(document='211')))
        self.remote.pages['999'] = page
        with self.assertRaises(RemoteError) as caught:
            self.replica.synchronize(authoritative_bodies=False)
        self.assertEqual('replica_body_limit', caught.exception.code)
        self.assertFalse((self.replica.control / 'transaction.json').exists())
        self.assertEqual(baseline, (self.replica.control / 'manifest.json').read_bytes())

    def test_unowned_projection_obstruction_is_preserved_and_cannot_be_admitted(self):
        self.replica.synchronize()
        self.remote.pages['212'] = Page(Node('212', 'New', '210', 'page', '20', 1), '<p>new</p>')
        path = self.replica.base / 'pages/Knowledge/New.md'
        atomic_write(path, b'user-owned obstruction')
        with self.assertRaises(RemoteError) as caught:
            self.replica.synchronize()
        self.assertEqual('replica_path_collision', caught.exception.code)
        self.assertEqual(b'user-owned obstruction', path.read_bytes())
        self.assertEqual('replica_recovery_pending', self.replica.get_document(dict(document='211'))['code'])

    def test_case_only_rename_and_unowned_case_variant_are_portable(self):
        self.replica.synchronize()
        before, after = self.change('211', title='operations', version=2)
        self.replica.publish_verified(after, before)
        self.assertIn('operations.md', self.metadata('manifest.json')['pages']['211']['path'])
        self.remote.pages['212'] = Page(Node('212', 'New', '210', 'page', '20', 1), '<p>new</p>')
        obstruction = self.replica.base / 'pages/Knowledge/NEW.md'
        atomic_write(obstruction, b'preserved unowned case variant')
        with self.assertRaises(RemoteError) as caught:
            self.replica.synchronize()
        self.assertEqual('replica_path_collision', caught.exception.code)
        self.assertEqual(b'preserved unowned case variant', obstruction.read_bytes())

    def test_whole_space_cache_keeps_parentless_and_visible_children_without_parent_access(self):
        settings = self.setting(root=None)
        remote = Native(settings)
        replica = Replica(settings, remote)
        remote.denied.add('210')
        remote.pages['212'] = Page(Node('212', 'Parentless', None, 'page', '20', 1), '<p>Independent tree</p>')
        replica.synchronize()
        self.assertIn('Shared evidence', replica.get_document(dict(document='211'))['content'])
        self.assertIn('Independent tree', replica.get_document(dict(document='212'))['content'])
        self.assertNotIn('210', remote.reads)
        data = json.loads((replica.control / 'manifest.json').read_text('utf-8'))
        self.assertEqual('', data['homePageId'])
        self.assertTrue(data['wholeSpace'])
        self.assertTrue(data['pages']['211']['path'].endswith('Operations~211.md'))

    def test_writer_lock_blocks_read_without_releasing_cross_process_lease(self):
        self.replica.synchronize()
        lock = self.replica.control / 'sync.lock'
        script = ('import sys; sys.path.insert(0,sys.argv[1]); from wiki_platform import file_lock\n'
                  'try:\n with file_lock(sys.argv[2]): print("acquired")\n'
                  'except BlockingIOError: print("busy")\n')
        with file_lock(lock):
            self.assertEqual('replica_busy', self.replica.get_document(dict(document='211'))['code'])
            result = subprocess.run([sys.executable, '-c', script, str(Path(wiki_platform.__file__).parent), str(lock)], capture_output=True, text=True, timeout=10)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual('busy', result.stdout.strip())
        self.assertIn('content', self.replica.get_document(dict(document='211')))

    def test_comment_watch_shared_cadence_quiet_baseline_and_failure_fingerprint_retention(self):
        self.replica.settings = self.setting(watch='211')
        self.remote.comments['211'] = [dict(id='310', kind='inline', version=1, status='current', inlineContext=dict(resolutionStatus='open'))]
        self.replica.synchronize()
        self.assertEqual('ready', self.replica.status()['commentWatch'])
        self.assertEqual(0, self.metadata('changes.json')['head'])
        self.remote.comment_reads.clear()
        other = Replica(self.setting(watch='211'), self.remote)
        other.poll_comments()
        self.assertEqual([], self.remote.comment_reads)
        self.remote.comment_error['211'] = RemoteError('http_503', 'Synthetic failed')
        self.replica.poll_comments(interval_seconds=0)
        self.assertEqual('unavailable', self.replica.status()['commentWatch'])
        before = self.metadata('manifest.json')['wikiCommentWatch']['211']['fingerprint']
        self.remote.comment_error.clear()
        self.remote.comments['211'][0]['inlineContext']['resolutionStatus'] = 'resolved'
        self.replica.poll_comments(interval_seconds=0)
        self.assertNotEqual(before, self.metadata('manifest.json')['wikiCommentWatch']['211']['fingerprint'])
        self.assertEqual('comments', self.metadata('changes.json')['events'][-1]['kind'])
        self.assertEqual(1, self.metadata('changes.json')['head'])

    def test_comment_budget_rotates_unattempted_pages_before_completed(self):
        for id in ['212', '213']:
            self.remote.pages[id] = Page(Node(id, id, '210', 'page', '20', 1), '<p>page</p>')
        self.replica.synchronize()
        self.remote.comment_error['212'] = RemoteError('wiki_request_limit', 'Budget exhausted')
        self.replica.poll_comments(['211', '212', '213'], 0)
        self.assertEqual(['211', '212'], self.remote.comment_reads)
        self.remote.comment_error.clear()
        self.remote.comment_reads.clear()
        self.replica.poll_comments(['211', '212', '213'], 0)
        self.assertEqual('212', self.remote.comment_reads[0])

    def test_actual_remote_watch_page_timeout_keeps_fingerprint_and_advances_healthy_peer(self):
        from unittest.mock import patch
        settings = self.setting(watch='210,211')
        pages = dict(self.remote.pages)
        slow, clock, footer_reads = [False], [time.monotonic()], []
        def transport(method, target, headers, data, timeout):
            self.assertEqual('GET', method)
            uri = urlsplit(target)
            match = re.fullmatch(r'/wiki/api/v2/pages/(210|211)(?:/(direct-children|footer-comments|inline-comments))?', uri.path)
            if match is None:
                return 404, {}, b'{}'
            id, operation = match.groups()
            if operation in ('footer-comments', 'inline-comments'):
                if operation == 'footer-comments':
                    footer_reads.append((id, timeout))
                    if slow[0] and id == '210':
                        clock[0] += 8.5
                value = dict(results=[])
            elif operation == 'direct-children':
                value = dict(results=[dict(id=p.node.id, type=p.node.type) for p in pages.values() if p.node.parent_id == id])
            else:
                page = pages[id]
                node = page.node
                value = dict(id=id, type='page', title=node.title, parentId=node.parent_id,
                             status='current', spaceId='20', version=dict(number=1))
                if parse_qs(uri.query).get('body-format') == ['storage']:
                    value['body'] = dict(storage=dict(value=page.body, representation='storage'))
            return 200, {}, compact_json(value).encode('utf-8')
        replica = Replica(settings, Remote(settings.credentials, transport=transport))
        replica.synchronize()
        before = self.metadata('manifest.json')['wikiCommentWatch']
        footer_reads.clear()
        slow[0], clock[0] = True, time.monotonic()
        with patch('time.monotonic', side_effect=lambda: clock[0]):
            replica.poll_comments(interval_seconds=0)
        current = self.metadata('manifest.json')['wikiCommentWatch']
        self.assertLessEqual(next(timeout for id, timeout in footer_reads if id == '210'), 8)
        self.assertIn('211', [id for id, _ in footer_reads])
        self.assertEqual('unavailable', current['210']['status'])
        self.assertEqual(before['210']['fingerprint'], current['210']['fingerprint'])
        self.assertEqual('ready', current['211']['status'])

    def test_relation_extraction_only_explicit_authored_links_and_verified_native_sources(self):
        body = ('<h2>지식 연결</h2><p>근거: <ac:link><ri:page ri:content-id="211" /></ac:link> — Observed deployment</p>'
                '<p>Ordinary <a href="https://elsewhere.test">link</a></p>')
        self.remote.pages['212'] = Page(Node('212', 'Conclusion', '210', 'page', '20', 1), body)
        self.replica.synchronize()
        graph = self.replica.relations(dict(document='211'))
        self.assertEqual(['212'], graph['affectedCandidates'])
        self.assertEqual(1, graph['relations'][0]['version'])
        self.assertFalse(graph['complete'])
        self.change('212', '<p>Native source no longer claims that premise</p>')
        stale = self.replica.relations(dict(document='211'))
        self.assertEqual([], stale['relations'])
        self.assertTrue(stale['syncRequired'])

    def test_inline_formatted_relation_link_does_not_make_valid_storage_unreadable(self):
        body = ('<h2>지식 연결</h2><p>근거: <strong><ac:link><ri:page ri:content-id="211" />'
                '</ac:link></strong> — <em>Observed deployment</em></p>')
        self.remote.pages['212'] = Page(Node('212', 'Conclusion', '210', 'page', '20', 1), body)
        self.replica.synchronize()
        self.assertTrue(self.replica.status()['ready'])
        self.assertEqual(dict(items=[dict(type='evidence', reason='Observed deployment', to='211')], complete=True),
                         knowledge_relations(body, self.settings.site, self.settings.space))

    def test_graph_rechecks_previously_consumed_endpoint_before_public_service_returns(self):
        from wiki_atlassian.service import WikiService
        body = '<h2>지식 연결</h2><p>근거: <ac:link><ri:page ri:content-id="211" /></ac:link> — Observed deployment</p>'
        self.remote.pages['212'] = Page(Node('212', 'Conclusion', '210', 'page', '20', 1), body)
        self.replica.synchronize()
        def change_earlier_endpoint(id):
            if id == '212':
                self.change('211', title='Changed target', version=2)
        self.remote.after_body = change_earlier_endpoint
        result = WikiService(self.settings, self.remote, self.replica).execute('document_relations', dict(document='211'))
        self.assertEqual('scope_metadata_changed', result['code'])
        self.assertNotIn('nodes', result)
        self.assertNotIn('affectedCandidates', result)

    def test_cold_relation_read_returns_native_start_with_sync_required_without_cache_edges(self):
        result = self.replica.relations(dict(document='211'))
        self.assertEqual([dict(id='211', title='Operations', version=1)], result['nodes'])
        self.assertEqual([], result['relations'])
        self.assertTrue(result['syncRequired'])
        self.assertFalse(result['complete'])


if __name__ == '__main__':
    unittest.main()
