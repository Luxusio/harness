"""Real filesystem publication, admission, and synthetic native-source contracts."""
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'wiki-atlassian'))
sys.path.insert(0, str(ROOT / 'plugin/scripts'))
from wiki_atlassian.config import SpaceSettings
from wiki_atlassian.models import Credentials, Node, Page, RemoteError, compact_json
from wiki_atlassian.remote import RootScopeDenial
from wiki_atlassian.replica import Replica, knowledge_relations
from wiki_platform import atomic_write, file_lock


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

    def comment_inventory(self, target, expected_node=None, scope=None):
        self.comment_reads.append(target)
        if target in self.comment_error:
            raise self.comment_error[target]
        return dict(provider='confluence', target=target, comments=self.comments.get(target, []), complete=True)


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
            result = subprocess.run([sys.executable, '-c', script, str(ROOT / 'plugin/scripts'), str(lock)], capture_output=True, text=True, timeout=10)
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

    def test_cold_relation_read_returns_native_start_with_sync_required_without_cache_edges(self):
        result = self.replica.relations(dict(document='211'))
        self.assertEqual([dict(id='211', title='Operations', version=1)], result['nodes'])
        self.assertEqual([], result['relations'])
        self.assertTrue(result['syncRequired'])
        self.assertFalse(result['complete'])


if __name__ == '__main__':
    unittest.main()
