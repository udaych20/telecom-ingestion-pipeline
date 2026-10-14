"""Dependency-free local REST service that exercises the production pipeline."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .contracts import build_change_event
from .pipeline import LiveIntentPipeline
from .settings import load_local_environment

load_local_environment()


class Handler(BaseHTTPRequestHandler):
    pipeline: LiveIntentPipeline

    def reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self.reply(200, {"status": "ok"})
            return
        self.reply(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/demo/infer":
            self.reply(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))

            if {"id", "source", "type", "data"}.issubset(payload):
                event = payload
            else:
                event = build_change_event("demo", payload)

            self.reply(200, self.pipeline.process(event))
        except (ValueError, json.JSONDecodeError) as error:
            self.reply(400, {"error": str(error)})
        except Exception as error:
            self.reply(502, {"error": str(error)})

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    Handler.pipeline = LiveIntentPipeline()
    host = os.getenv("DEMO_HOST", "127.0.0.1")
    port = int(os.getenv("DEMO_PORT", "8080"))
    address = (host, port)

    print(
        f"Live intent demo listening on http://{host}:{port}",
        flush=True,
    )
    ThreadingHTTPServer(address, Handler).serve_forever()


if __name__ == "__main__":
    main()
