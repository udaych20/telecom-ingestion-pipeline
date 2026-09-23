"""Shared Cosmos interaction joins for batch and intent exports."""

import json
from datetime import datetime, timezone
from pipeline_logging import LOGGER, progress


def query(container, field, values):
    records = []
    for value in values:
        sql = f"SELECT * FROM c WHERE c.{field} = @value"
        params = [{"name": "@value", "value": value}]
        records.extend(container.query_items(sql, parameters=params, enable_cross_partition_query=True))
    return list(records)


def query_feedback(container, cids):
    records = []
    sql = """
        SELECT * FROM c
        WHERE EXISTS (
            SELECT VALUE feedback
            FROM feedback IN c.feedbacks
            WHERE ARRAY_CONTAINS(feedback.cid_list, @cid)
        )
    """
    for cid in cids:
        params = [{"name": "@cid", "value": cid}]
        records.extend(container.query_items(sql, parameters=params, enable_cross_partition_query=True))
    return remove_duplicates(records)


def query_chat(container, cid):
    sql = """
        SELECT * FROM c
        WHERE EXISTS (
            SELECT VALUE message
            FROM message IN c.messages
            WHERE message.data.cid = @cid
        )
    """
    params = [{"name": "@cid", "value": cid}]
    return list(container.query_items(sql, parameters=params, enable_cross_partition_query=True))


def find_values(data, field):
    values = []
    if isinstance(data, dict):
        for key, value in data.items():
            if key == field and value:
                values.extend(value if isinstance(value, list) else [value])
            values.extend(find_values(value, field))
    elif isinstance(data, list):
        for value in data:
            values.extend(find_values(value, field))
    return list(dict.fromkeys(str(value) for value in values))


def remove_duplicates(records):
    unique = {}
    for record in records:
        key = record.get("id", json.dumps(record, sort_keys=True))
        unique[key] = record
    return list(unique.values())


def get_interaction(
    database, cid, *,
    chat_container="chat-history-uat",
    tools_container="context-history-all-tools",
    context_container="context-history-uat",
    feedback_container="chat-feedback",
):
    chat = database.get_container_client(chat_container)
    tools = database.get_container_client(tools_container)
    context = database.get_container_client(context_container)
    feedback_source = database.get_container_client(feedback_container)

    with progress("interaction chat query"):
        chats = query_chat(chat, cid)
    if not chats:
        raise ValueError(f"Chat not found for cid: {cid}")

    with progress("interaction context query"):
        context_history = query(context, "run_id", [cid])
    run_ids = find_values(context_history, "run_id")
    with progress("interaction tool queries"):
        tool_history = query(tools, "run_id", run_ids) if run_ids else []

    with progress("interaction feedback query"):
        feedback = query_feedback(feedback_source, [cid])
    LOGGER.info("Interaction records: chat=%s context=%s tools=%s feedback=%s",
                len(chats), len(context_history), len(tool_history), len(feedback))

    return {
        "interaction_id": cid,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "cids": [cid],
        "run_ids": run_ids,
        "chat_history": chats,
        "tool_history": remove_duplicates(tool_history),
        "context_history": remove_duplicates(context_history),
        "feedback": feedback,
    }
