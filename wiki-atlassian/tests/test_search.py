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
