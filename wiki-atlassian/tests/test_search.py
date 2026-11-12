import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wiki_atlassian.search import Query, authored_start, projected_headings


class SearchTests(unittest.TestCase):
    def test_ranks_title_phrase_title_terms_body_phrase_body_terms_and_split_terms(self):
        rows = [('한 운영', '본문', 6, 'title'), ('오늘 한 운영 기록', '본문', 5, 'title'),
                ('운영 및 한', '본문', 4, 'title'), ('기록', '한 운영 절차', 3, 'body'),
                ('기록', '운영 절차 한', 2, 'body'), ('한 보고서', '운영 절차', 1, 'title_and_body')]
        for title, body, rank, field in rows:
            with self.subTest(title=title):
                match = Query('한 운영').match(title, body)
                self.assertEqual((rank, field), (match.rank, match.field))
        self.assertIsNone(Query('한 미존재').match('한 보고서', '운영 절차'))

    def test_nfc_matches_preserve_authored_combining_jamo_and_astral_offsets(self):
        content = '😀 Cafe\u0301 😀 한 운영'
        cafe = Query('CAFÉ').match('기록', content)
        self.assertEqual(content.index('Cafe'), cafe.body_offset)
        korean = Query('한 운영').match('기록', content)
        self.assertEqual(content.index('ᄒ'), korean.body_offset)
        self.assertTrue(content[korean.body_offset:].startswith('한 운영'))

    def test_numbers_do_not_match_digits_inside_other_numbers(self):
        self.assertIsNone(Query('201').match('2015 results', '12010 2015'))
        self.assertEqual(3, Query('201').match('numbers', 'native 201 id').rank)

    def test_replica_header_excluded_but_ordinary_authored_frontmatter_remains(self):
        prefix = '---\npage_id: "201"\nversion: 1\nurl: https://example.test/wiki/pages/201\n---\n\n'
        self.assertEqual(len(prefix), authored_start(prefix+'Authored text'))
        self.assertEqual(0, authored_start('---\ntitle: example\n---\nAuthored text'))

    def test_projected_heading_path_ignores_fences_and_keeps_full_outline_for_native_verification(self):
        text = '## Work\n### Approval\nEvidence\n```\n# example\n```\n## Later\n'
        outline, path = projected_headings(text, 0, text.index('Evidence'))
        self.assertEqual([(2, 'Work'), (3, 'Approval'), (2, 'Later')], outline)
        self.assertEqual(['Work', 'Approval'], path)
        self.assertIsNone(projected_headings('## Work\n```\ntext', 0, 16))


if __name__ == '__main__':
    unittest.main()


class ReplicaSearchTests(unittest.TestCase):
    def setUp(self):
        import test_replica as fixtures
        fixtures.ReplicaTests.setUp(self)

    def setting(self, *args, **kwargs):
        import test_replica as fixtures
        return fixtures.ReplicaTests.setting(self, *args, **kwargs)

    def metadata(self, name):
        import test_replica as fixtures
        return fixtures.ReplicaTests.metadata(self, name)

    def test_multilingual_ranked_pages_return_original_snippet_and_verifiable_heading_path(self):
        from dataclasses import replace
        from wiki_atlassian.models import Node, Page
        self.remote.pages['211'] = Page(replace(self.remote.pages['211'].node, title='운영 기록'),
            '<h2>업무</h2><h3>승인</h3><p>Cafe\u0301 😀 한 운영 절차와 승인 기록. 후속 내용</p>')
        self.remote.pages['212'] = Page(Node('212', '한 운영', '210', 'page', '20', 1), '<p>Mentions 211 and 733</p>')
        self.remote.pages['213'] = Page(Node('213', '한 보고서', '210', 'page', '20', 1), '<p>운영 기준</p>')
        self.replica.synchronize()
        self.remote.reads.clear()
        first = self.replica.search(dict(query='한 운영', limit=1, snippetLength=14))
        self.assertEqual('212', first['results'][0]['id'])
        second = self.replica.search(dict(query='한 운영', limit=1, snippetLength=14, cursor=first['nextCursor']))
        hit = second['results'][0]
        self.assertEqual('211', hit['id'])
        self.assertEqual(['업무', '승인'], hit['headingPath'])
        self.assertIn('😀 한 운영', hit['excerpt'])
        self.assertLessEqual(len(hit['excerpt']), 14)
        self.assertEqual(hit['excerpt'], self.replica.get_document(hit['read'])['content'])
        third = self.replica.search(dict(query='한 운영', limit=1, snippetLength=14, cursor=second['nextCursor']))
        self.assertEqual('title_and_body', third['results'][0]['match'])
        self.assertFalse(third['truncated'])
        for absent in ['page_id:', 'example.test', '2115', '한 미존재']:
            self.assertEqual([], self.replica.search(dict(query=absent))['results'])
        self.assertEqual('211', self.replica.search(dict(query='CAFÉ'))['results'][0]['id'])
        self.assertEqual([], self.remote.reads)

    def test_snapshot_rejects_same_version_storage_projection_markdown_and_native_version_changes(self):
        from dataclasses import replace
        from wiki_atlassian.models import Page
        from wiki_platform import atomic_write
        self.replica.synchronize()
        locator = self.replica.search(dict(query='211'))['results'][0]['read']
        prior = self.remote.pages['211']
        current = Page(prior.node, '<p>Same-version replacement evidence</p>')
        self.remote.pages['211'] = current
        self.replica.publish_verified(current, prior)
        self.remote.reads.clear()
        self.assertEqual('snapshot_changed', self.replica.get_document(locator)['code'])
        current_locator = self.replica.search(dict(query='211'))['results'][0]['read']
        self.assertNotEqual(locator['snapshot'], current_locator['snapshot'])
        self.assertEqual(locator['version'], current_locator['version'])
        row = self.metadata('manifest.json')['pages']['211']
        path = self.replica.base / row['path']
        atomic_write(path, path.read_bytes().replace(b'replacement', b'reprojected'))
        self.assertEqual('snapshot_changed', self.replica.get_document(current_locator)['code'])
        latest = self.replica.search(dict(query='211'))['results'][0]['read']
        self.assertEqual('snapshot_changed', self.replica.get_document(dict(latest, version=2))['code'])
        self.assertEqual('invalid_snapshot', self.replica.get_document(dict(latest, section='1'))['code'])
        self.assertEqual('invalid_snapshot', self.replica.get_document(dict(latest, format='storage'))['code'])
        self.assertEqual([], self.remote.reads)

    def test_cursor_binds_query_limits_root_and_every_markdown_projection(self):
        from wiki_atlassian.models import Node, Page
        from wiki_platform import atomic_write
        self.remote.pages['212'] = Page(Node('212', 'Another', '210', 'page', '20', 1), '<p>Shared evidence</p>')
        self.replica.synchronize()
        args = dict(query='Shared', limit=1, snippetLength=14)
        first = self.replica.search(args)
        cursor = first['nextCursor']
        for change in [dict(query='evidence'), dict(limit=2), dict(snippetLength=15), dict(root='211')]:
            self.assertEqual('snapshot_changed', self.replica.search(dict(args, cursor=cursor, **change))['code'])
        row = self.metadata('manifest.json')['pages']['212']
        path = self.replica.base / row['path']
        atomic_write(path, path.read_bytes()+b' changed projection')
        self.assertEqual('snapshot_changed', self.replica.search(dict(args, cursor=cursor))['code'])

    def test_projection_heading_promoted_from_native_paragraph_has_no_native_heading_claim(self):
        from wiki_atlassian.models import Page
        node = self.remote.pages['211'].node
        self.remote.pages['211'] = Page(node, '<h2>True header</h2><p>## Literal paragraph</p><p>topic-probe</p>')
        self.replica.synchronize()
        hit = self.replica.search(dict(query='topic-probe'))['results'][0]
        self.assertNotIn('headingPath', hit)
        self.assertIn('topic-probe', hit['excerpt'])

    def test_exact_id_dominates_mentions_but_respects_scoped_subtree(self):
        from wiki_atlassian.models import Node, Page
        self.remote.pages['212'] = Page(Node('212', 'Another', '210', 'page', '20', 1), '<p>Mentions 211 and 733</p>')
        self.replica.synchronize()
        hit = self.replica.search(dict(query='211'))['results'][0]
        self.assertEqual(('211', 'native_id'), (hit['id'], hit['match']))
        other = self.replica.search(dict(query='211', root='212'))['results'][0]
        self.assertEqual(('212', 'body'), (other['id'], other['match']))
        self.assertEqual([], self.replica.search(dict(query='733', root='999'))['results'])

    def test_unicode_continuations_require_version_and_preserve_exact_codepoints(self):
        self.replica.synchronize()
        full = self.replica.get_document(dict(document='211'))['content']
        pieces, offset = [], 0
        while True:
            result = self.replica.get_document(dict(document='211', version=1, offset=offset, length=7))
            pieces.append(result['content'])
            if not result['truncated']:
                break
            offset = result['nextOffset']
        self.assertEqual(full, ''.join(pieces))
        self.assertEqual('invalid_query', self.replica.get_document(dict(document='211', offset=1))['code'])
        self.assertEqual('invalid_query', self.replica.search(dict(query='Shared', limit=26))['code'])
        self.assertEqual('invalid_query', self.replica.search(dict(query=' '*3))['code'])
