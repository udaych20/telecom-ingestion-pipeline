import argparse
import json
import os
import sys
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
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
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "10"))


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


def get_interaction(database, cid):
    chat = database.get_container_client(CHAT_CONTAINER)
    tools = database.get_container_client(TOOLS_CONTAINER)
    context = database.get_container_client(CONTEXT_CONTAINER)
    feedback_container = database.get_container_client(FEEDBACK_CONTAINER)

    chats = query_chat(chat, cid)
    if not chats:
        raise ValueError(f"Chat not found for cid: {cid}")

    tool_history = query(tools, "cid", [cid])
    run_ids = find_values(tool_history, "run_id")
    context_history = query(context, "run_id", run_ids) if run_ids else []

    feedback = query_feedback(feedback_container, [cid])

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


def live_event_values(data, field):
    value = data.get(field, [])
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    return [
        str(item) for item in values if item is not None and str(item).strip()
    ]


def resolve_live_event_cids(database, event):
    data = event["data"]
    cids = live_event_values(data, "cids")
    cids.extend(live_event_values(data, "cid_lists"))
    if cids:
        return list(dict.fromkeys(cids))

    run_ids = live_event_values(data, "run_ids")
    if not run_ids:
        return []
    tools = database.get_container_client(TOOLS_CONTAINER)
    tool_records = query(tools, "run_id", run_ids)
    return find_values(tool_records, "cid")


def log_unmatched_live_event(event):
    data = event["data"]
    path = os.path.join(OUTPUT_DIR, "unmatched_live_events.csv")
    append_csv(
        path,
        ["event_id", "source", "container", "document_id", "reason"],
        [(
            event.get("id", ""),
            event.get("source", ""),
            data.get("container", ""),
            data.get("document_id", ""),
            "No cid could be resolved from the change notification",
        )],
    )


def process_live_event(database, event):
    cids = resolve_live_event_cids(database, event)
    if not cids:
        log_unmatched_live_event(event)
        print(f"Skipped uncorrelated live event {event['id']}")
        return

    for cid in cids:
        interaction = get_interaction(database, cid)
        interaction["live_event"] = {
            "id": event["id"],
            "source": event.get("source", ""),
            "type": event.get("type", ""),
            "time": event.get("time", ""),
            "container": event["data"].get("container", ""),
            "document_id": event["data"].get("document_id", ""),
        }
        save_interaction(interaction)
        print(f"Processed live interaction {cid} from event {event['id']}")


def save_interaction(interaction, output_format="both"):
    if output_format in ("csv", "both"):
        save_interaction_csv(interaction)
    if output_format in ("jsonl", "both"):
        path = os.path.join(OUTPUT_DIR, "interactions.jsonl")
        with open(path, "a", encoding="utf-8") as file:
            file.write(json.dumps(interaction, default=str) + "\n")

    if INGESTION_MODE == "llm":
        ingest_for_llm(interaction)
    elif INGESTION_MODE == "knowledge_graph":
        ingest_for_graph(interaction)
    elif INGESTION_MODE != "none":
        raise ValueError("INGESTION_MODE must be none, llm, or knowledge_graph")


def get_chat_id_batches(database, start_time=None, end_time=None):
    chat = database.get_container_client(CHAT_CONTAINER)
    filters = ["IS_DEFINED(message.data.cid)"]
    parameters = []
    if start_time is not None:
        filters.append("c._ts >= @start_ts")
        parameters.append({"name": "@start_ts", "value": int(start_time.timestamp())})
    if end_time is not None:
        filters.append("c._ts <= @end_ts")
        parameters.append({"name": "@end_ts", "value": int(end_time.timestamp())})

    sql = f"""
        SELECT message.data.cid AS cid, c._ts AS ts
        FROM c
        JOIN message IN c.messages
        WHERE {' AND '.join(filters)}
        ORDER BY c._ts DESC
    """
    rows = chat.query_items(
        sql,
        parameters=parameters,
        enable_cross_partition_query=True,
    )
    batch = []
    count = 0
    seen = set()
    for row in rows:
        interaction_id = row.get("cid")
        if not interaction_id or interaction_id in seen:
            continue
        seen.add(interaction_id)
        if BATCH_LIMIT and count >= BATCH_LIMIT:
            break
        batch.append(interaction_id)
        count += 1
        if len(batch) == BATCH_SIZE:
            yield batch
            batch = []
    if batch:
        yield batch


def cosmos_ts_to_iso(value):
    if value is None:
        return "unknown"
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def print_container_timestamps(database):
    print("Cosmos container latest timestamps (UTC):")
    for name in (CHAT_CONTAINER, TOOLS_CONTAINER, CONTEXT_CONTAINER, FEEDBACK_CONTAINER):
        container = database.get_container_client(name)
        rows = list(container.query_items(
            "SELECT TOP 1 c.id, c._ts FROM c ORDER BY c._ts DESC",
            enable_cross_partition_query=True,
        ))
        if rows:
            row = rows[0]
            print(f"  {name}: {cosmos_ts_to_iso(row.get('_ts'))} (id={row.get('id', '')})")
        else:
            print(f"  {name}: EMPTY")


def has_all_containers(interaction):
    sources = ("chat_history", "tool_history", "context_history", "feedback")
    return all(interaction[source] for source in sources)


def missing_containers(interaction):
    sources = ("chat_history", "tool_history", "context_history", "feedback")
    return [source for source in sources if not interaction[source]]


def log_failure(interaction_id, error):
    path = os.path.join(OUTPUT_DIR, "failed_interactions.csv")
    append_csv(path, ["interaction_id", "error"], [(interaction_id, str(error))])


def process_batch(database, interaction_ids):
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_id = {
            executor.submit(get_interaction, database, interaction_id): interaction_id
            for interaction_id in interaction_ids
        }
        for future in as_completed(future_to_id):
            interaction_id = future_to_id[future]
            try:
                yield interaction_id, future.result(), None
            except Exception as error:
                yield interaction_id, None, error


def parse_iso_time(value):
    normalized = value.strip()
    if normalized.endswith(("Z", "z")):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "must be an ISO 8601 time, for example 2026-09-04T18:00:00+05:30"
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError(
            "must include a timezone offset, for example +05:30 or Z"
        )
    return parsed.astimezone(timezone.utc)


def parse_arguments(arguments):
    parser = argparse.ArgumentParser(
        description="Export correlated telecom interactions from Cosmos DB."
    )
    parser.add_argument("chat_id", nargs="?", help="one chat CID to export")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--all", action="store_true", help="export all matching chats")
    modes.add_argument(
        "--all-complete",
        action="store_true",
        help="export matching chats only when all four sources are present",
    )
    modes.add_argument("--timestamps", action="store_true")
    modes.add_argument("--live", action="store_true")
    parser.add_argument(
        "--start-time",
        type=parse_iso_time,
        help="inclusive Cosmos document _ts lower bound (ISO 8601 with timezone)",
    )
    parser.add_argument(
        "--end-time",
        type=parse_iso_time,
        help="inclusive Cosmos document _ts upper bound; defaults to process start",
    )
    parser.add_argument(
        "--output-format",
        choices=("csv", "jsonl", "both"),
        default="both",
        help="assembled interaction output format (default: both)",
    )
    args = parser.parse_args(arguments)

    selected_modes = sum(
        bool(value)
        for value in (args.chat_id, args.all, args.all_complete, args.timestamps, args.live)
    )
    if selected_modes != 1:
        parser.error(
            "choose exactly one chat ID, --all, --all-complete, --timestamps, or --live"
        )
    if args.start_time is not None and not (args.all or args.all_complete):
        parser.error("--start-time is supported only with --all or --all-complete")
    if args.end_time is not None and args.start_time is None:
        parser.error("--end-time requires --start-time")
    if args.start_time is not None:
        args.end_time = args.end_time or datetime.now(timezone.utc)
        if args.start_time > args.end_time:
            parser.error("--start-time must be earlier than or equal to --end-time")
    return args


if __name__ == "__main__":
    args = parse_arguments(sys.argv[1:])
    if not ENDPOINT:
        raise ValueError("COSMOS_ENDPOINT is required")
    if BATCH_LIMIT < 0 or BATCH_SIZE < 1 or MAX_WORKERS < 1:
        raise ValueError("BATCH_LIMIT must be >= 0; BATCH_SIZE and MAX_WORKERS must be > 0")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    credential = DefaultAzureCredential()
    client = CosmosClient(ENDPOINT, credential=credential)
    database = client.get_database_client(DATABASE)

    if args.live:
        from live_stream import run_live_stream

        try:
            run_live_stream(lambda event: process_live_event(database, event), credential)
        finally:
            client.close()
            credential.close()
        raise SystemExit(0)

    if args.timestamps:
        print_container_timestamps(database)
        client.close()
        credential.close()
        raise SystemExit(0)

    all_mode = args.all or args.all_complete
    complete_only = args.all_complete
    if all_mode:
        print_container_timestamps(database)
    if args.start_time is not None:
        print(
            "Filtering chat documents by Cosmos _ts (UTC, inclusive): "
            f"{args.start_time.isoformat()} through {args.end_time.isoformat()}"
        )
    batches = (
        get_chat_id_batches(database, args.start_time, args.end_time)
        if all_mode
        else [[args.chat_id]]
    )

    print(f"Parallel workers: {MAX_WORKERS}")
    success = 0
    skipped = 0
    failed = 0
    for batch_number, interaction_ids in enumerate(batches, start=1):
        batch_success = 0
        batch_skipped = 0
        missing_counts = {"tool_history": 0, "context_history": 0, "feedback": 0}
        print(f"Processing batch {batch_number} ({len(interaction_ids)} chats, {MAX_WORKERS} workers)")

        for interaction_id, interaction, error in process_batch(database, interaction_ids):
            if error is not None:
                failed += 1
                log_failure(interaction_id, error)
                print(f"Failed {interaction_id}: {error}")
                continue

            if complete_only and not has_all_containers(interaction):
                skipped += 1
                batch_skipped += 1
                for source in missing_containers(interaction):
                    if source in missing_counts:
                        missing_counts[source] += 1
                continue

            # Writes stay on the main thread so concurrent workers never write
            # to the same CSV/JSONL files at the same time.
            save_interaction(interaction, args.output_format)
            success += 1
            batch_success += 1
            print(f"Processed {interaction_id}")

        if complete_only:
            missing = ", ".join(f"missing_{name}={count}" for name, count in missing_counts.items())
            print(f"Batch {batch_number}: complete={batch_success}, skipped={batch_skipped}, {missing}")

    client.close()
    credential.close()
    print(f"Completed: {success} succeeded, {skipped} skipped, {failed} failed")
