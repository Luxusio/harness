import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'plugin/scripts'))
from wiki_atlassian import changes


def row(version=1, hash='a'*64, title='private'):
    return dict(type='page', status='current', version=version, bodyHash=hash, title=title, path='pages/x.md')


def manifest(pages=None):
    return dict(inventoryComplete=True, pages=pages or {}, unavailable=[])


class ChangesTests(unittest.TestCase):
    def test_first_complete_inventory_quiet_then_documents_sequence_without_private_text(self):
        baseline = changes.documents({}, manifest({'1': row()}))
        self.assertEqual([], baseline['wikiNotices']['events'])
        next = changes.documents(baseline, manifest({'1': row(2), '2': row()}))
        self.assertEqual([dict(seq=1, kind='updated', id='1', version=2), dict(seq=2, kind='created', id='2', version=1)], next['wikiNotices']['events'])
        self.assertEqual(baseline['wikiNotices']['epoch'], next['wikiNotices']['epoch'])
        self.assertNotIn('private', str(next['wikiNotices']))
        changes.validate(next)

    def test_scope_loss_prunes_events_and_readmission_is_updated(self):
        first = changes.documents({}, manifest({'1': row()}))
        updated = changes.documents(first, manifest({'1': row(2)}))
        denied = changes.documents(updated, dict(manifest({'1': row(2)}), unavailable=['1']))
        self.assertEqual([dict(seq=2, kind='scope')], denied['wikiNotices']['events'])
        recovered = changes.documents(denied, manifest({'1': row(2)}))
        self.assertEqual(dict(seq=3, kind='updated', id='1', version=2), recovered['wikiNotices']['events'][-1])

    def test_bulk_changes_count_all_sequences_and_retain_only_64(self):
        previous = changes.documents({}, manifest())
        result = changes.documents(previous, manifest({str(i): row() for i in range(100, 200)}))
        feed = result['wikiNotices']
        self.assertEqual(100, feed['head'])
        self.assertEqual(64, len(feed['events']))
        self.assertEqual(dict(seq=37, kind='created', id='136', version=1), feed['events'][0])
        changes.validate(result)

    def test_same_version_hash_title_and_path_each_observable(self):
        result = changes.documents({}, manifest({'1': row()}))
        for key, value in [('bodyHash', 'b'*64), ('title', 'renamed'), ('path', 'pages/renamed.md')]:
            next = copy.deepcopy(result)
            next['pages']['1'][key] = value
            result = changes.documents(result, next)
        self.assertEqual(3, result['wikiNotices']['head'])

    def test_comment_metadata_fingerprint_ignores_bodies_authors_order_but_tracks_replies_resolution(self):
        a = dict(comments=[dict(id='2', kind='inline', version=1, status='current', body='secret', authorId='secret', inlineContext=dict(resolutionStatus='open')),
                           dict(id='1', kind='footer', version=1, status='current')])
        b = copy.deepcopy(a)
        b['comments'].reverse()
        b['comments'][1]['body'] = 'different body'
        b['comments'][1]['authorId'] = 'different author'
        self.assertEqual(changes.comment_fingerprint(a), changes.comment_fingerprint(b))
        b['comments'][1]['inlineContext']['resolutionStatus'] = 'resolved'
        self.assertNotEqual(changes.comment_fingerprint(a), changes.comment_fingerprint(b))
        b = copy.deepcopy(a)
        b['comments'].append(dict(id='3', kind='footer', parentId='1', version=1, status='current'))
        self.assertNotEqual(changes.comment_fingerprint(a), changes.comment_fingerprint(b))

    def test_comment_baseline_quiet_failure_preserves_fingerprint_then_recovers_once(self):
        base = changes.documents({}, manifest({'1': row(), '2': row()}))
        base = changes.comments(base, 100, ['1', '2'], {'1': 'a'*64, '2': 'b'*64})
        self.assertEqual('ready', changes.comment_status(base, ['1', '2']))
        self.assertEqual(0, base['wikiNotices']['head'])
        failed = changes.comments(base, 200, ['1', '2'], {'1': 'c'*64})
        self.assertEqual('unavailable', changes.comment_status(failed, ['1', '2']))
        self.assertEqual('b'*64, failed['wikiCommentWatch']['2']['fingerprint'])
        restored = changes.comments(failed, 300, ['2'], {'2': 'd'*64})
        self.assertEqual([e['id'] for e in restored['wikiNotices']['events']], ['1', '2'])
        unchanged = changes.comments(restored, 400, ['2'], {'2': 'd'*64})
        self.assertEqual(restored['wikiNotices'], unchanged['wikiNotices'])

    def test_shared_comment_checkpoints_bound_64_keep_new_attempt_after_clock_reversal(self):
        base = changes.documents({}, manifest({str(i): row() for i in range(100, 166)}))
        for i in range(100, 165):
            base = changes.comments(base, i, [str(i)], {str(i): 'a'*64})
        self.assertEqual(64, len(base['wikiCommentWatch']))
        self.assertIsNone(changes.comment_attempted_at(base, '100'))
        base = changes.comments(base, 1, ['165'], {'165': 'b'*64})
        self.assertIn('165', base['wikiCommentWatch'])
        self.assertEqual(64, len(base['wikiCommentWatch']))
        self.assertTrue(changes.comment_due(base, '164', 2, 1000))
        changes.validate(base)

    def test_watch_config_exact_native_ids(self):
        self.assertEqual(['11', '12'], changes.watch_pages('12,11'))
        self.assertEqual([], changes.watch_pages(None))
        for invalid in ['', '1,1', '1, 2', '0', '1,', '1'*33, ','.join(str(i) for i in range(1, 12))]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                changes.watch_pages(invalid)

    def test_feed_rejects_sequence_schema_types_and_unadmitted_identity(self):
        base = changes.documents(changes.documents({}, manifest()), manifest({'1': row()}))
        for change in [lambda f: f.update(head=True), lambda f: f.update(epoch='x'*32),
                       lambda f: f['events'][0].update(seq=0), lambda f: f['events'][0].update(id='2'),
                       lambda f: f['events'][0].update(version=True), lambda f: f['events'][0].update(title='leak')]:
            broken = copy.deepcopy(base)
            change(broken['wikiNotices'])
            with self.assertRaises(ValueError):
                changes.validate(broken)

    def test_hook_consumes_exact_legacy_feed_shape(self):
        import wiki_change_notice
        result = changes.documents(changes.documents({}, manifest()), manifest({'1': row()}))
        epoch, head, events = wiki_change_notice._events(result['wikiNotices'])
        self.assertEqual(1, head)
        self.assertEqual(result['wikiNotices']['epoch'], epoch)
        self.assertEqual('created', events[0]['kind'])


if __name__ == '__main__':
    unittest.main()
