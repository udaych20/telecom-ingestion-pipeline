import csv
import json
from pathlib import Path
import tempfile
import unittest

from large_interaction_viewer import build_index, query_index, TEXT_CHUNK, prepare_overview, viewer_page


class LargeViewerTests(unittest.TestCase):
    def test_pages_and_complete_long_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.csv'
            index = Path(folder) / 'viewer.sqlite3'
            fields = ['interaction_id', 'source', 'data', 'input', 'output', 'intent']
            large = 'quote" comma, newline\n日本語' * 4000
            with source.open('w', encoding='utf-8-sig', newline='') as file:
                writer = csv.DictWriter(file, fieldnames=fields)
                writer.writeheader()
                for n in range(25):
                    writer.writerow(dict(interaction_id='001', source='tool_history', intent='rca' if n < 20 else 'query',
                                         data=json.dumps({'value': n}), input=large if n == 0 else '', output='done'))
            original = source.read_bytes()
            build_index(source, index)
            build_index(source, index)
            self.assertEqual(source.read_bytes(), original)
            cids = query_index(index, '/api/cids', {})
            self.assertEqual(cids['rows'], [{'cid': '001', 'count': 25}])
            first = query_index(index, '/api/records', {'cid': ['001']})
            self.assertIsNone(first['rows'][0]['preview'])
            self.assertIsNotNone(first['rows'][1]['preview'])
            self.assertEqual(first['counts'], {'tool_history': 25})
            self.assertEqual(query_index(index, '/api/records', {'cid': ['001'], 'source': ['chat_history']})['rows'], [])
            self.assertEqual(len(first['rows']), 20)
            self.assertTrue(first['more'])
            last = query_index(index, '/api/records', {'cid': ['001'], 'page': ['1']})
            self.assertEqual(len(last['rows']), 5)
            self.assertFalse(last['more'])
            self.assertEqual(query_index(index, '/api/intents', {})['rows'], ['query', 'rca'])
            filtered = query_index(index, '/api/records', {'cid': ['001'], 'intent': ['query']})
            self.assertEqual(len(filtered['rows']), 5)
            self.assertTrue(all(row['intent'] == 'query' for row in filtered['rows']))
            self.assertEqual(query_index(index, '/api/cids', {'intent': ['missing']})['rows'], [])
            chunks, offset = [], 0
            while True:
                part = query_index(index, '/api/text', {'id': ['1'], 'offset': [str(offset)]})
                self.assertLessEqual(len(part['text']), TEXT_CHUNK)
                chunks.append(part['text'])
                offset = part['next']
                if not part['more']:
                    break
            self.assertEqual(json.loads(''.join(chunks))['input'], large)
            prepare_overview(index)
            prepare_overview(index)
            self.assertEqual(query_index(index, '/api/overview', {})['documents'], 0)
            self.assertEqual(query_index(index, '/api/cids', {'q': ["' OR 1=1 --"]})['rows'], [])
            with source.open('a', encoding='utf-8') as file:
                file.write('\n')
            with self.assertRaisesRegex(ValueError, 'does not match'):
                build_index(source, index)

    def test_rejects_invalid_csv_and_source_as_index(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.csv'
            source.write_text('wrong\nvalue\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                build_index(source, source)
            with self.assertRaises(ValueError):
                build_index(source, Path(folder) / 'bad.sqlite3')

    def test_reuses_original_renderers(self):
        page = viewer_page()
        self.assertIn('function renderChatMessages', page)
        self.assertIn('function renderContextHistory', page)
        self.assertIn('function renderFeedback', page)
        self.assertIn('Filter by source', page)
        self.assertIn('Filter by intent', page)
        self.assertNotIn('file.stream()', page)
