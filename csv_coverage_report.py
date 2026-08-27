import argparse
import csv
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone


SOURCES = ("chat_history", "context_history", "tool_history", "feedback")
REFERENCED_SOURCES = ("context_history", "tool_history", "feedback")
COVERAGE_FIELDS = [
    "interaction_id",
    "chat_history_records",
    "context_history_records",
    "tool_history_records",
    "feedback_records",
    "has_context_history",
    "has_tool_history",
    "has_feedback",
    "referenced_container_count",
    "complete_all_four_containers",
]


def record_key(row):
    if row.get("record_id"):
        return f"id:{row['record_id']}"
    digest = hashlib.sha256((row.get("data") or "").encode("utf-8")).hexdigest()
    return f"data:{digest}"


def scan_csv_to_index(source_path, connection, progress_every=100_000):
    connection.execute("""
        CREATE TABLE records (
            interaction_id TEXT NOT NULL,
            source TEXT NOT NULL,
            record_key TEXT NOT NULL,
            PRIMARY KEY (interaction_id, source, record_key)
        ) WITHOUT ROWID
    """)
    insert = "INSERT OR IGNORE INTO records VALUES (?, ?, ?)"
    total_rows = 0
    accepted_rows = 0
    source_size = os.path.getsize(source_path)

    with open(source_path, newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        required = {"interaction_id", "source", "data"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Missing required CSV columns: {', '.join(sorted(missing))}")

        batch = []
        for row in reader:
            total_rows += 1
            interaction_id = row.get("interaction_id")
            source = row.get("source")
            if interaction_id and source in SOURCES:
                batch.append((interaction_id, source, record_key(row)))
                accepted_rows += 1
            if len(batch) >= 10_000:
                connection.executemany(insert, batch)
                connection.commit()
                batch.clear()
            if progress_every and total_rows % progress_every == 0:
                read_bytes = min(file.buffer.tell(), source_size)
                percent = (read_bytes / source_size * 100) if source_size else 100
                print(f"Scanned {total_rows:,} rows ({percent:.1f}%)")

        if batch:
            connection.executemany(insert, batch)
            connection.commit()
    return total_rows, accepted_rows


def read_coverage_rows(connection):
    columns = ", ".join(
        f"SUM(CASE WHEN source = '{source}' THEN 1 ELSE 0 END) AS {source}_records"
        for source in SOURCES
    )
    query = f"""
        SELECT interaction_id, {columns}
        FROM records
        GROUP BY interaction_id
        ORDER BY interaction_id
    """
    rows = []
    for result in connection.execute(query):
        counts = dict(zip(SOURCES, result[1:]))
        rows.append({
            "interaction_id": result[0],
            **{f"{source}_records": counts[source] for source in SOURCES},
            "has_context_history": int(counts["context_history"] > 0),
            "has_tool_history": int(counts["tool_history"] > 0),
            "has_feedback": int(counts["feedback"] > 0),
            "referenced_container_count": sum(counts[source] > 0 for source in REFERENCED_SOURCES),
            "complete_all_four_containers": int(all(counts.values())),
        })
    return rows


def build_summary(rows, source_path, total_rows, accepted_rows):
    analyzed = len(rows)
    source_metrics = {}
    for source in SOURCES:
        field = f"{source}_records"
        matched = sum(row[field] > 0 for row in rows)
        source_metrics[source] = {
            "cids_with_match": matched,
            "cids_without_match": analyzed - matched,
            "coverage_percent": round(matched / analyzed * 100, 2) if analyzed else 0.0,
            "total_records": sum(row[field] for row in rows),
        }
    complete = sum(row["complete_all_four_containers"] for row in rows)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "derived_from_csv": os.path.abspath(source_path),
        "source_csv_rows_scanned": total_rows,
        "source_csv_rows_accepted": accepted_rows,
        "distinct_chat_cids_analyzed": analyzed,
        "complete_all_four_containers": complete,
        "incomplete_interactions": analyzed - complete,
        "complete_coverage_percent": round(complete / analyzed * 100, 2) if analyzed else 0.0,
        "cids_by_referenced_container_count": {
            str(count): sum(row["referenced_container_count"] == count for row in rows)
            for count in range(4)
        },
        "sources": source_metrics,
        "limitation": "Metrics include only interaction IDs present in the source CSV.",
    }


def write_reports(rows, summary, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    detail_path = os.path.join(output_dir, "interaction_coverage.csv")
    with open(detail_path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=COVERAGE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    json_path = os.path.join(output_dir, "coverage_summary.json")
    with open(json_path, "w", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2)
        file.write("\n")

    summary_rows = [
        ("overall", "source_csv_rows_scanned", summary["source_csv_rows_scanned"]),
        ("overall", "source_csv_rows_accepted", summary["source_csv_rows_accepted"]),
        ("overall", "distinct_chat_cids_analyzed", summary["distinct_chat_cids_analyzed"]),
        ("overall", "complete_all_four_containers", summary["complete_all_four_containers"]),
        ("overall", "incomplete_interactions", summary["incomplete_interactions"]),
        ("overall", "complete_coverage_percent", summary["complete_coverage_percent"]),
    ]
    for source, metrics in summary["sources"].items():
        summary_rows.extend((source, metric, value) for metric, value in metrics.items())
    for count, value in summary["cids_by_referenced_container_count"].items():
        summary_rows.append(("relationship_distribution", f"cids_matching_{count}_of_3_referenced_containers", value))
    csv_path = os.path.join(output_dir, "coverage_summary.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["section", "metric", "value"])
        writer.writerows(summary_rows)
    return detail_path, csv_path, json_path


def generate_coverage_report(source_path, output_dir, progress_every=100_000):
    if not os.path.exists(source_path):
        raise FileNotFoundError(f"CSV not found: {source_path}")
    os.makedirs(output_dir, exist_ok=True)
    index_path = os.path.join(output_dir, ".csv_coverage_index.sqlite3")
    if os.path.exists(index_path):
        os.remove(index_path)
    connection = None
    try:
        connection = sqlite3.connect(index_path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        total_rows, accepted_rows = scan_csv_to_index(source_path, connection, progress_every)
        rows = read_coverage_rows(connection)
        connection.close()
        connection = None
        summary = build_summary(rows, source_path, total_rows, accepted_rows)
        paths = write_reports(rows, summary, output_dir)
        return summary, paths
    finally:
        if connection is not None:
            connection.close()
        for suffix in ("", "-wal", "-shm"):
            path = index_path + suffix
            if os.path.exists(path):
                os.remove(path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build container coverage reports from interactions.csv without Cosmos DB.")
    parser.add_argument("csv_path", nargs="?", default=os.path.join("output", "interactions.csv"))
    parser.add_argument("--output-dir", default="output")
    args = parser.parse_args()

    summary, paths = generate_coverage_report(args.csv_path, args.output_dir)
    print(f"Analyzed {summary['distinct_chat_cids_analyzed']:,} interaction IDs")
    print(f"Complete: {summary['complete_all_four_containers']:,}; incomplete: {summary['incomplete_interactions']:,}")
    for path in paths:
        print(f"Wrote {path}")
