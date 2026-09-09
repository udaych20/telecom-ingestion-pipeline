import unittest
from unittest.mock import MagicMock, call, patch

import app


class LiveCorrelationTests(unittest.TestCase):
    def test_direct_event_cids_are_deduplicated(self):
        event = {"data": {"cids": ["c1", "c1"], "cid_lists": ["c2"]}}
        self.assertEqual(
            ["c1", "c2"], app.resolve_live_event_cids(MagicMock(), event)
        )

    @patch("app.query")
    def test_run_ids_resolve_through_all_tools(self, query):
        database = MagicMock()
        tools = object()
        database.get_container_client.return_value = tools
        query.return_value = [{"cid": "c1", "run_id": "r1"}]
        event = {"data": {"run_ids": ["r1"]}}

        self.assertEqual(["c1"], app.resolve_live_event_cids(database, event))
        query.assert_called_once_with(tools, "run_id", ["r1"])

    @patch("app.query_feedback", return_value=[{"id": "feedback"}])
    @patch("app.query_chat", return_value=[{"id": "chat"}])
    @patch("app.query")
    def test_interaction_queries_tools_by_cid_then_context_by_run_id(
        self, query, _query_chat, _query_feedback
    ):
        database = MagicMock()
        chat, tools, context, feedback = object(), object(), object(), object()
        database.get_container_client.side_effect = [chat, tools, context, feedback]

        def query_side_effect(container, field, values):
            if container is tools:
                return [{"id": "tool", "cid": "c1", "run_id": "r1"}]
            if container is context:
                return [{"id": "context", "run_id": "r1"}]
            self.fail("Unexpected container")

        query.side_effect = query_side_effect
        interaction = app.get_interaction(database, "c1")

        self.assertEqual(["r1"], interaction["run_ids"])
        self.assertEqual(
            [call(tools, "cid", ["c1"]), call(context, "run_id", ["r1"])],
            query.call_args_list,
        )


if __name__ == "__main__":
    unittest.main()
