import unittest

from app import classify_interaction
from intent_app import classify


class RcaCompletionTests(unittest.TestCase):
    def test_completed_flag_overrides_missing_text_and_ticket(self):
        prediction = classify({'rca_complete': True, 'message': 'create a ticket'}, active_ticket=True)
        self.assertEqual(prediction.intent, 'rca')
        self.assertEqual(prediction.rule, 'rca.completed_source_flag')
        self.assertFalse(prediction.needs_human_review)

    def test_supported_source_locations(self):
        for record in ({'rca_complete': ' true '},
                       {'conversation': {'rca_complete': True}},
                       {'messages': [{'data': {'rca_complete': True}}]}):
            with self.subTest(record=record):
                self.assertEqual(classify(record).intent, 'rca')

    def test_false_and_missing_use_existing_rules(self):
        for value in (False, 'false', None, 1, 'yes'):
            self.assertEqual(classify({'rca_complete': value, 'message': 'create a ticket'}).intent, 'ticket')
        self.assertEqual(classify({'rca_complete': False}).intent, 'clarification_needed')

    def test_chat_flag_is_not_hidden_by_agent_records(self):
        source, labels, intents = classify_interaction({
            'interaction_id': 'cid', 'chat_history': [{'rca_complete': True}],
            'context_history': [{'agent': 'Network', 'function_name': 'check'}],
        })
        self.assertEqual(source, 'chat_history')
        self.assertEqual(intents, ['rca'])

