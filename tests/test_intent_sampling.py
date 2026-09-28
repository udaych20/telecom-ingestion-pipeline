import csv
import argparse
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from intent_app import IntentSampler, SAMPLE_INTENTS, export_selected_cids, write_intent_count_csv, run_initial_load


def row(intent, index=0):
    return {'classification.intent': intent, 'conversation_id': str(index), 'source_id': str(index),
            'classification.needs_human_review': False}


class IntentSamplingTests(unittest.TestCase):
    @patch('intent_app.find_missing_cosmos_records')
    @patch('intent_app.label_records')
    @patch('intent_app.load_cosmos_records')
    @patch('intent_app.CosmosClient')
    @patch('intent_app.check_cosmos_authentication')
    @patch('intent_app.create_pipeline_credential')
    def test_timeframe_path_samples_and_splits_without_missing_audit(self, credential, auth, client, load, classify, audit):
        load.return_value = [{'id': 'source'}]
        classify.return_value = [row(intent, i) for intent in SAMPLE_INTENTS for i in range(3)]
        with tempfile.TemporaryDirectory() as directory:
            env = {'COSMOS_ENDPOINT': 'https://example.documents.azure.com', 'COSMOS_DATABASE': 'test',
                   'COSMOS_CONTAINER': 'source', 'CSV_OUTPUT_DIR': directory,
                   'INTENT_SAMPLING_ENABLED': 'true', 'INTENT_SAMPLES_PER_INTENT': '1',
                   'INTENT_FIND_MISSING': 'true', 'TRAINING_PLACEHOLDER_ENABLED': 'false',
                   'INTENT_SEPARATE_CLARIFICATION': 'true'}
            with patch.dict(os.environ, env, clear=True), patch('builtins.print'):
                run_initial_load(argparse.Namespace(start_time=None, end_time=None))
            with (Path(directory) / 'intent_labels_all.csv').open(encoding='utf-8-sig', newline='') as source:
                self.assertEqual(len(list(csv.DictReader(source))), 5)
            with (Path(directory) / 'intent_clarification_needed.csv').open(encoding='utf-8-sig', newline='') as source:
                self.assertEqual(len(list(csv.DictReader(source))), 1)
        audit.assert_not_called()

    def test_each_intent_is_capped_independently(self):
        sampler = IntentSampler(2)
        labels = [row(intent, i) for intent in SAMPLE_INTENTS for i in range(5)]
        self.assertEqual(len(sampler.select(labels)), 12)
        self.assertTrue(sampler.full())
        self.assertEqual(sampler.select(labels), [])

    def test_concurrent_workers_cannot_exceed_quota(self):
        sampler = IntentSampler(1000)
        with ThreadPoolExecutor(max_workers=10) as pool:
            groups = list(pool.map(lambda _: sampler.select([row('ticket')] * 200), range(20)))
        self.assertEqual(sum(map(len, groups)), 1000)
        self.assertFalse(sampler.full())

    def test_report_includes_missing_intents_and_shortfalls(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'counts.csv'
            write_intent_count_csv(path, [row('ticket')], sample_limit=1000)
            with path.open(encoding='utf-8-sig', newline='') as source:
                counts = {r['classification_intent']: r for r in csv.DictReader(source)}
            self.assertEqual(counts['ticket']['sample_shortfall'], '999')
            self.assertEqual(counts['rca']['sample_shortfall'], '1000')
            self.assertEqual(counts['ALL_INTENTS']['requested_samples'], '6000')

    @patch('intent_app.fetch_interaction_rows')
    @patch('intent_app.label_records')
    @patch('intent_app.load_cosmos_records')
    def test_only_selected_rows_are_enriched_and_next_batch_is_skipped(self, load, classify, interactions):
        load.return_value = [{'id': 'source'}]
        classify.return_value = [row(intent, i) for intent in SAMPLE_INTENTS for i in range(3)]
        interactions.side_effect = lambda database, rows: [dict(r, **{'interaction.chat_history': '[]'}) for r in rows]
        output = Mock()
        _, labels = export_selected_cids(Mock(), Mock(), ['first', 'second'], output,
                                        sampler=IntentSampler(1), batch_size=1, workers=1,
                                        include_interactions=True)
        load.assert_called_once()
        self.assertEqual(len(interactions.call_args.args[1]), 6)
        self.assertEqual(len(labels), 6)
        self.assertTrue(all('interaction.chat_history' in r for r in output.append.call_args.args[0]))

    @patch('intent_app.fetch_interaction_rows')
    @patch('intent_app.label_records')
    @patch('intent_app.load_cosmos_records')
    def test_over_quota_cids_do_not_fetch_interactions(self, load, classify, interactions):
        load.return_value = [{'id': 'source'}]
        classify.return_value = [row('ticket')]
        interactions.side_effect = lambda database, rows: rows
        _, labels = export_selected_cids(Mock(), Mock(), ['first', 'second'], Mock(),
                                        sampler=IntentSampler(1), batch_size=1, workers=1,
                                        include_interactions=True)
        self.assertEqual(len(labels), 1)
        interactions.assert_called_once()
        self.assertEqual(load.call_count, 2)

    def test_invalid_quota_rejected(self):
        for limit in (0, -1):
            with self.assertRaises(ValueError):
                IntentSampler(limit)
