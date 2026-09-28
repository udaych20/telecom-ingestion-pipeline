import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from azure.core.messaging import CloudEvent
from azure.eventgrid.models import AcknowledgeResult, BrokerProperties, ReceiveDetails, ReleaseResult


def load_app(transport):
    path = Path(__file__).resolve().parents[1] / 'event_app' / 'function_app.py'
    spec = importlib.util.spec_from_file_location('test_event_app_' + transport, path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {'STREAM_TRANSPORT': transport}):
        spec.loader.exec_module(module)
    return module


class EventGridTests(unittest.TestCase):
    def setUp(self):
        self.module = load_app('eventgrid')
        self.event = self.module.build_event('chat', {'id': 'doc', '_etag': 'version', 'cid': 'cid1'})
        self.detail = ReceiveDetails(event=CloudEvent.from_dict(self.event),
                                    broker_properties=BrokerProperties(lock_token='lock1', delivery_count=1))
        self.client = Mock()
        self.client.receive.return_value = [self.detail]
        self.client.acknowledge.return_value = AcknowledgeResult(succeeded_lock_tokens=['lock1'], failed_lock_tokens=[])
        self.client.release.return_value = ReleaseResult(succeeded_lock_tokens=['lock1'], failed_lock_tokens=[])

    def test_grid_registers_timer_not_event_hub_trigger(self):
        functions = self.module.app.get_functions()
        types = [binding.type for function in functions for binding in function.get_bindings()]
        self.assertIn('timerTrigger', types)
        self.assertNotIn('eventHubTrigger', types)
        self.assertEqual(types.count('cosmosDBTrigger'), 4)

    def test_eventhub_registration_preserved(self):
        functions = load_app('eventhub').app.get_functions()
        types = [binding.type for function in functions for binding in function.get_bindings()]
        self.assertIn('eventHubTrigger', types)
        self.assertNotIn('timerTrigger', types)

    def test_success_acknowledges_after_processing(self):
        with patch.object(self.module, 'process_event') as process:
            self.client.acknowledge.side_effect = lambda **kwargs: (
                process.assert_called_once(), AcknowledgeResult(succeeded_lock_tokens=['lock1'], failed_lock_tokens=[]))[1]
            self.module.consume_grid_events(self.client)
        self.client.acknowledge.assert_called_once_with(lock_tokens=['lock1'])
        self.client.release.assert_not_called()

    def test_processing_failure_releases_without_ack(self):
        with patch.object(self.module, 'process_event', side_effect=ValueError('invalid')):
            with self.assertRaisesRegex(RuntimeError, '1 processing'):
                self.module.consume_grid_events(self.client)
        self.client.acknowledge.assert_not_called()
        self.client.release.assert_called_once_with(lock_tokens=['lock1'], release_delay=10)

    def test_failed_ack_is_not_reported_as_success(self):
        self.client.acknowledge.return_value = AcknowledgeResult(succeeded_lock_tokens=[], failed_lock_tokens=[])
        with self.assertRaisesRegex(RuntimeError, '1 processing'):
            self.module.consume_grid_events(self.client)
        self.client.release.assert_called_once()

    def test_failure_does_not_skip_later_messages(self):
        second = ReceiveDetails(event=CloudEvent.from_dict(self.event),
                                broker_properties=BrokerProperties(lock_token='lock2', delivery_count=1))
        self.client.receive.return_value.append(second)
        self.client.acknowledge.return_value = AcknowledgeResult(succeeded_lock_tokens=['lock2'], failed_lock_tokens=[])
        with patch.object(self.module, 'process_event', side_effect=[ValueError('invalid'), None]):
            with self.assertRaises(RuntimeError):
                self.module.consume_grid_events(self.client)
        self.client.acknowledge.assert_called_once_with(lock_tokens=['lock2'])

    def test_empty_poll(self):
        self.client.receive.return_value = []
        self.module.consume_grid_events(self.client)
        self.client.acknowledge.assert_not_called()

    def test_receive_failure_propagates_without_ack(self):
        self.client.receive.side_effect = RuntimeError('network failure')
        with self.assertRaisesRegex(RuntimeError, 'network failure'):
            self.module.consume_grid_events(self.client)
        self.client.acknowledge.assert_not_called()

    def test_publish_failure_propagates_to_cosmos_trigger(self):
        publisher = Mock()
        publisher.publish.side_effect = RuntimeError('publish failed')
        with patch.object(self.module, 'get_publisher', return_value=publisher):
            with self.assertRaisesRegex(RuntimeError, 'publish failed'):
                self.module.publish_documents([{'id': 'doc', '_etag': 'version'}], 'chat')

    def test_publisher_uses_namespace_topic_and_cloud_event(self):
        env = {'EVENT_GRID_ENDPOINT': 'https://existing-grid.westus2-1.eventgrid.azure.net', 'EVENT_GRID_TOPIC': 'updates'}
        with patch.dict(os.environ, env), patch('azure.identity.DefaultAzureCredential'), patch('azure.eventgrid.EventGridPublisherClient') as client:
            publisher = self.module.EventGridPublisher()
            publisher.publish(self.event, 'cid1')
        self.assertEqual(client.call_args.kwargs['namespace_topic'], 'updates')
        client.return_value.send.assert_called_once_with(self.event)

    def test_transport_selection(self):
        with patch.object(self.module, 'EventGridPublisher') as grid, patch.object(self.module, 'EventHubPublisher') as hub:
            self.module.get_publisher()
        grid.assert_called_once()
        hub.assert_not_called()

    def test_bad_batch_size_is_rejected_before_receive(self):
        with patch.dict(os.environ, {'EVENT_GRID_RECEIVE_BATCH_SIZE': '101'}):
            with self.assertRaises(ValueError):
                self.module.consume_grid_events(self.client)
        self.client.receive.assert_not_called()

    def test_unknown_transport_rejected(self):
        with self.assertRaisesRegex(ValueError, 'STREAM_TRANSPORT'):
            load_app('wrong')
