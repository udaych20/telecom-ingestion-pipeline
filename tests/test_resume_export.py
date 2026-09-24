import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from intent_app import IncrementalOutput, export_selected_cids
from resume_export import ResumeExport


class ResumeTests(unittest.TestCase):
    @patch('intent_app.load_cosmos_records')
    def test_workers_have_separate_clients(self, load):
        barrier = threading.Barrier(2)
        containers = []
        clients = []

        def fetch(container, *args, **kwargs):
            containers.append(container)
            barrier.wait(timeout=5)
            return []

        def factory():
            client = Mock()
            clients.append(client)
            return client, Mock(), Mock()

        load.side_effect = fetch
        export_selected_cids(Mock(), Mock(), ['a', 'b'], Mock(),
                             workers=2, worker_factory=factory)
        self.assertEqual(len(clients), 2)
        self.assertIsNot(containers[0], containers[1])
        for client in clients:
            client.close.assert_called_once()

    def test_legacy_split_import_and_append(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / 'main.csv', Path(directory) / 'clarify.csv']
            output = IncrementalOutput(*paths)
            output.append([{'conversation_id': '001', 'classification.intent': 'query'},
                           {'conversation_id': '002', 'classification.intent': 'clarification_needed'}])
            checkpoint = ResumeExport(paths, True, {})
            self.assertEqual(checkpoint.completed, {'001', '002'})
            output = IncrementalOutput(*paths, resume=True)
            output.append([{'conversation_id': '003', 'classification.intent': 'query'}])
            checkpoint.mark('003', 1)
            self.assertEqual(len(list(checkpoint.read_rows())), 3)
            checkpoint.close()

    def test_uncommitted_rows_retried_and_empty_cid_remembered(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'main.csv'
            checkpoint = ResumeExport([path], False, {})
            output = IncrementalOutput(path)
            output.append([{'conversation_id': 'done'}])
            checkpoint.mark('done', 1)
            checkpoint.mark('empty', 0)
            output.append([{'conversation_id': 'unfinished'}])
            checkpoint.close()
            checkpoint = ResumeExport([path], True, {})
            self.assertEqual(checkpoint.completed, {'done', 'empty'})
            self.assertEqual([r['conversation_id'] for r in checkpoint.read_rows()], ['done'])
            checkpoint.close()

    def test_scope_change_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'main.csv'
            IncrementalOutput(path)
            ResumeExport([path], False, {'container': 'a'}).close()
            with self.assertRaisesRegex(ValueError, 'configuration differs'):
                ResumeExport([path], True, {'container': 'b'})

    @patch('intent_app.load_cosmos_records')
    def test_failure_retains_checkpoint_and_closes_worker(self, load):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'main.csv'
            checkpoint = ResumeExport([path], False, {})
            output = IncrementalOutput(path)
            load.side_effect = [[], RuntimeError('failed')]
            client, database, container = Mock(), Mock(), Mock()
            factory = Mock(return_value=(client, database, container))
            with self.assertRaisesRegex(RuntimeError, 'saved rows remain'):
                export_selected_cids(Mock(), Mock(), ['empty', 'failed'], output,
                                     workers=1, batch_size=1, checkpoint=checkpoint,
                                     worker_factory=factory)
            self.assertEqual(checkpoint.completed, {'empty'})
            factory.assert_called_once()
            client.close.assert_called_once()
            self.assertIs(load.call_args.args[0], container)
            checkpoint.close()
