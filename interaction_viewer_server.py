import argparse
import json
import os
import sqlite3
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class ViewerHandler(SimpleHTTPRequestHandler):
    db_path = None

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            try:
                if parsed.path == "/api/status":
                    return self.send_json({"ok": True, "db": os.path.abspath(self.db_path)})
                if parsed.path == "/api/coverage":
                    return self.handle_coverage()
                if parsed.path == "/api/interaction":
                    return self.handle_interaction(parse_qs(parsed.query))
                self.send_error(404, "Unknown API endpoint")
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=500)
            return
        if parsed.path in ("/", ""):
            self.path = "/interactions-viewer.html"
        return super().do_GET()

    def connect(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def handle_coverage(self):
        with self.connect() as connection:
            coverage = [dict(row) for row in connection.execute("SELECT * FROM coverage ORDER BY interaction_id")]
            summary_row = connection.execute("SELECT payload FROM summary LIMIT 1").fetchone()
            summary = json.loads(summary_row["payload"]) if summary_row else {}
        self.send_json({"coverage": coverage, "summary": summary})

    def handle_interaction(self, query):
        interaction_id = (query.get("id") or [""])[0]
        if not interaction_id:
            return self.send_json({"error": "Missing id query parameter"}, status=400)
        with self.connect() as connection:
            rows = [dict(row) for row in connection.execute(
                "SELECT interaction_id, source, record_id, cid, run_id, data FROM records WHERE interaction_id = ? ORDER BY source, record_id",
                (interaction_id,),
            )]
        self.send_json({"interaction_id": interaction_id, "records": rows})

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Serve the disk-backed NORA interaction viewer on localhost.")
    parser.add_argument("--db", default=os.path.join("output", "interactions_viewer.sqlite3"))
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    if not os.path.exists(args.db):
        raise FileNotFoundError(f"Viewer database not found: {args.db}. Run csv_coverage_report.py first.")

    ViewerHandler.db_path = os.path.abspath(args.db)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), ViewerHandler)
    print(f"NORA viewer running at http://127.0.0.1:{args.port}/")
    print(f"Using database: {ViewerHandler.db_path}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
