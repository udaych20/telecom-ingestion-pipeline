import json
import os
import sys
import csv
from datetime import datetime, timezone

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv


load_dotenv()


ENDPOINT = os.getenv("COSMOS_ENDPOINT")
DATABASE = os.getenv("COSMOS_DATABASE", "NORA")

CHAT_CONTAINER = os.getenv("COSMOS_CHAT_CONTAINER", "chat-history-uat")
TOOLS_CONTAINER = os.getenv("COSMOS_TOOLS_CONTAINER", "context-history-all-tools")
CONTEXT_CONTAINER = os.getenv("COSMOS_CONTEXT_CONTAINER", "context-history-uat")
FEEDBACK_CONTAINER = os.getenv("COSMOS_FEEDBACK_CONTAINER", "chat-feedback")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "output")
INGESTION_MODE = os.getenv("INGESTION_MODE", "none").lower()
BATCH_LIMIT = int(os.getenv("BATCH_LIMIT", "0"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "100"))


def query(container, field, values):
    records = []
    for value in values:
        sql = f"SELECT * FROM c WHERE c.{field} = @value"
        params = [{"name": "@value", "value": value}]
        records.extend(container.query_items(sql, parameters=params, enable_cross_partition_query=True))
    return list(records)


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


def save_interaction_csv(interaction):
    path = os.path.join(OUTPUT_DIR, "interactions.csv")
    fields = ["interaction_id", "cid", "run_id", "source", "record_id", "data"]

    file_exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        if not file_exists:
            writer.writeheader()

        for source in ("chat_history", "tool_history", "context_history", "feedback"):
            for record in interaction[source]:
                writer.writerow({
                    "interaction_id": interaction["interaction_id"],
                    "cid": record.get("cid", ",".join(interaction["cids"])),
                    "run_id": record.get("run_id", ""),
                    "source": source,
                    "record_id": record.get("id", ""),
                    "data": json.dumps(record, ensure_ascii=False, default=str),
                })


def first_value(data, names):
    for name in names:
        values = find_values(data, name)
        if values:
            return values[0]
    return ""


def ingest_for_llm(interaction):
    path = os.path.join(OUTPUT_DIR, "llm_training.jsonl")
    for chat in interaction["chat_history"]:
        user_text = first_value(chat, ["user_content", "user_message", "query", "issue"])
        assistant_text = first_value(chat, ["assistant_content", "assistant_response", "response"])
        if not user_text or not assistant_text:
            continue

        sample = {
            "messages": [
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": assistant_text},
            ],
            "metadata": {"interaction_id": interaction["interaction_id"], "cids": interaction["cids"]},
        }
        with open(path, "a", encoding="utf-8") as file:
            file.write(json.dumps(sample, ensure_ascii=False) + "\n")


def ingest_for_graph(interaction):
    nodes_path = os.path.join(OUTPUT_DIR, "graph_nodes.csv")
    edges_path = os.path.join(OUTPUT_DIR, "graph_edges.csv")
    interaction_id = interaction["interaction_id"]

    nodes = [(interaction_id, "Interaction")]
    edges = []
    for cid in interaction["cids"]:
        nodes.append((cid, "Conversation"))
        edges.append((interaction_id, cid, "HAS_CONVERSATION"))
    for run_id in interaction["run_ids"]:
        nodes.append((run_id, "Run"))
        for cid in interaction["cids"]:
            edges.append((cid, run_id, "HAS_RUN"))

    append_csv(nodes_path, ["id", "label"], nodes)
    append_csv(edges_path, ["source", "target", "relationship"], edges)


def append_csv(path, headers, rows):
    file_exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        if not file_exists:
            writer.writerow(headers)
        writer.writerows(rows)


def get_interaction(database, interaction_id):
    chat = database.get_container_client(CHAT_CONTAINER)
    tools = database.get_container_client(TOOLS_CONTAINER)
    context = database.get_container_client(CONTEXT_CONTAINER)
    feedback_container = database.get_container_client(FEEDBACK_CONTAINER)

    chats = query(chat, "id", [interaction_id])
    if not chats:
        raise ValueError(f"Chat not found: {interaction_id}")

    cids = find_values(chats, "cid")
    if not cids:
        raise ValueError(f"No cid found in chat: {interaction_id}")

    tool_history = query(tools, "run_id", cids)
    context_history = query(context, "run_id", cids)
    run_ids = find_values(tool_history + context_history, "run_id")

    feedback = query(feedback_container, "cid", cids)

    return {
        "interaction_id": interaction_id,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "cids": cids,
        "run_ids": run_ids,
        "chat_history": chats,
        "tool_history": remove_duplicates(tool_history),
        "context_history": remove_duplicates(context_history),
        "feedback": feedback,
    }


def save_interaction(interaction):
    save_interaction_csv(interaction)
    path = os.path.join(OUTPUT_DIR, "interactions.jsonl")
    with open(path, "a", encoding="utf-8") as file:
        file.write(json.dumps(interaction, default=str) + "\n")

    if INGESTION_MODE == "llm":
        ingest_for_llm(interaction)
    elif INGESTION_MODE == "knowledge_graph":
        ingest_for_graph(interaction)
    elif INGESTION_MODE != "none":
        raise ValueError("INGESTION_MODE must be none, llm, or knowledge_graph")


def get_chat_id_batches(database):
    chat = database.get_container_client(CHAT_CONTAINER)
    sql = "SELECT VALUE c.id FROM c WHERE IS_DEFINED(c.id)"
    ids = chat.query_items(sql, enable_cross_partition_query=True)
    batch = []
    count = 0
    for interaction_id in ids:
        if BATCH_LIMIT and count >= BATCH_LIMIT:
            break
        batch.append(interaction_id)
        count += 1
        if len(batch) == BATCH_SIZE:
            yield batch
            batch = []
    if batch:
        yield batch


def has_all_containers(interaction):
    sources = ("chat_history", "tool_history", "context_history", "feedback")
    return all(interaction[source] for source in sources)


def log_failure(interaction_id, error):
    path = os.path.join(OUTPUT_DIR, "failed_interactions.csv")
    append_csv(path, ["interaction_id", "error"], [(interaction_id, str(error))])


if __name__ == "__main__":
    if not ENDPOINT or len(sys.argv) != 2:
        print("Usage: python app.py <chat-id> | --all | --all-complete")
        print("Set COSMOS_ENDPOINT and optionally COSMOS_DATABASE first.")
        raise SystemExit(1)
    if BATCH_LIMIT < 0 or BATCH_SIZE < 1:
        raise ValueError("BATCH_LIMIT must be >= 0 and BATCH_SIZE must be > 0")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    credential = DefaultAzureCredential()
    client = CosmosClient(ENDPOINT, credential=credential)
    database = client.get_database_client(DATABASE)

    all_mode = sys.argv[1] in ("--all", "--all-complete")
    complete_only = sys.argv[1] == "--all-complete"
    batches = get_chat_id_batches(database) if all_mode else [[sys.argv[1]]]

    success = 0
    skipped = 0
    failed = 0
    for batch_number, interaction_ids in enumerate(batches, start=1):
        print(f"Processing batch {batch_number} ({len(interaction_ids)} chats)")
        for interaction_id in interaction_ids:
            try:
                interaction = get_interaction(database, interaction_id)
                if complete_only and not has_all_containers(interaction):
                    skipped += 1
                    continue
                save_interaction(interaction)
                success += 1
                print(f"Processed {interaction_id}")
            except Exception as error:
                failed += 1
                log_failure(interaction_id, error)
                print(f"Failed {interaction_id}: {error}")

    client.close()
    credential.close()
    print(f"Completed: {success} succeeded, {skipped} skipped, {failed} failed")
