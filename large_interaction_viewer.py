"""Index a completed interactions CSV on disk and browse it on localhost."""

import argparse
import csv
import json
import sqlite3
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


PAGE_SIZE = 20
# Large individual cells are fetched separately, in bounded text chunks.
TEXT_CHUNK = 32000
PREVIEW_LIMIT = 64000


def viewer_page():
    """Reuse the standalone viewer's styles and record renderers."""
    original = Path(__file__).with_name('interactions-viewer.html').read_text(encoding='utf-8')
    head = original.split('</head>')[0] + '</head>'
    helpers = original.split('  function parseRowJSON(row){', 1)[1].split('})();', 1)[0]
    script = Path(__file__).with_name('large-viewer.js').read_text(encoding='utf-8')
    return head + '''<body>
    <header><h1>NORA Interaction Viewer</h1><p>Conversation and execution records · large-file, disk-backed mode</p></header>
    <div id="error" class="error" role="alert"></div>
    <section class="toolbar"><button id="find">Search conversations</button>
    <input id="search" placeholder="Search CID (not full-text)">
    <select id="source" aria-label="Filter by source"><option value="">All sources</option>
    <option value="chat_history">Chat history</option><option value="context_history">Context history (Agent)</option>
    <option value="tool_history">Tool history</option><option value="feedback">Feedback</option></select>
    <select id="intent" aria-label="Filter by intent"><option value="">All intents</option></select></section>
    <section id="feedback-overview" class="feedback-overview" style="display:block"></section>
    <main><aside><div class="aside-title" id="cid-title">Conversations</div><div id="cids"></div>
    <button id="prevCid">Previous</button><button id="nextCid">Next</button></aside>
    <section class="content"><div class="title-row"><h2 id="selected">Select a conversation</h2><div class="stats" id="stats"></div></div>
    <p class="meta" id="info"></p><button id="prevRow">Previous records</button><button id="nextRow">Next records</button>
    <div id="records"></div></section></main><script>(()=>{
    const state={query:''}, error=document.getElementById('error');
    const preferred=['agent','function_name','function_arguments','function_result','content','message','error'];
    function parseRowJSON(row){''' + helpers + script + '\n})();</script></body></html>'


def signature(path):
    stat = path.stat()
    return json.dumps([str(path.resolve()), stat.st_size, stat.st_mtime_ns])


def prepare_overview(index):
    """Cache feedback totals once, including for indexes made by older versions."""
    with closing(sqlite3.connect(index)) as db, db:
        if db.execute("SELECT 1 FROM metadata WHERE key='feedback_overview'").fetchone():
            return
        print('Preparing feedback overview on disk…', flush=True)
        db.execute('CREATE TEMP TABLE seen_feedback(cid TEXT,record_id TEXT,PRIMARY KEY(cid,record_id))')
        positive = negative = 0
        import hashlib
        for cid, record_id, payload in db.execute("SELECT cid,record_id,payload FROM records WHERE source='feedback'"):
            row = json.loads(payload)
            raw = row.get('data') or ''
            key = record_id or hashlib.sha256(raw.encode()).hexdigest()
            cursor = db.execute('INSERT OR IGNORE INTO seen_feedback VALUES(?,?)', (cid, key))
            if not cursor.rowcount:
                continue
            try:
                record = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not isinstance(record, dict):
                continue
            value = record.get('negative_feedbacks', record.get('negative_feedback'))
            is_negative = bool(value) if isinstance(value, (dict, list)) else False
            negative += int(is_negative)
            positive += int(not is_negative)
        db.execute("INSERT INTO metadata VALUES('feedback_overview',?)",
                   (json.dumps(dict(positive=positive, negative=negative, documents=positive + negative)),))


def build_index(source, destination):
    """Retain one CSV record at a time; reuse only a complete, matching index."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination:
        raise ValueError("Index path must differ from the CSV")
    expected = signature(source)
    if destination.exists():
        with closing(sqlite3.connect(destination)) as db:
            try:
                saved = db.execute("SELECT value FROM metadata WHERE key='source'").fetchone()
            except sqlite3.DatabaseError as error:
                raise ValueError("Index is not a completed viewer index; choose another --index path") from error
            if saved and saved[0] == expected:
                print(f"Reusing index: {destination}", flush=True)
                return
        raise ValueError("Index does not match this CSV; choose a new --index path")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Reserve without overwriting an existing file.
    with destination.open("xb"):
        pass
    csv.field_size_limit(2**31 - 1)
    with closing(sqlite3.connect(destination)) as db, db, source.open(encoding="utf-8-sig", newline="") as file:
        db.execute("PRAGMA cache_size=-8192")
        db.execute("PRAGMA temp_store=FILE")
        db.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)")
        db.execute("CREATE TABLE records(id INTEGER PRIMARY KEY,cid TEXT,source TEXT,record_id TEXT,"
                   "intent TEXT,agent TEXT,function_name TEXT,run_id TEXT,payload TEXT)")
        reader = csv.DictReader(file)
        if not {'interaction_id', 'source', 'data'} <= set(reader.fieldnames or []):
            raise ValueError("Expected interactions.csv with interaction_id, source and data columns")
        count = 0
        for count, row in enumerate(reader, 1):
            values = [row.get(key) or '' for key in
                      ('interaction_id', 'source', 'record_id', 'intent', 'agent', 'function_name', 'run_id')]
            db.execute("INSERT INTO records(cid,source,record_id,intent,agent,function_name,run_id,payload) "
                       "VALUES(?,?,?,?,?,?,?,?)", (*values, json.dumps(row, ensure_ascii=False)))
            if count % 1000 == 0:
                db.commit()
                print(f"Indexed {count:,} records", flush=True)
        print(f"Indexed {count:,} records; building CID lookup…", flush=True)
        db.execute("CREATE INDEX by_cid ON records(cid,id)")
        db.execute("CREATE TABLE conversations AS SELECT cid,COUNT(*) AS count FROM records GROUP BY cid")
        db.execute("CREATE UNIQUE INDEX conversation_cid ON conversations(cid)")
        if signature(source) != expected:
            raise ValueError("CSV changed during indexing; wait for export completion and use a new index path")
        db.execute("INSERT INTO metadata VALUES('source',?)", (expected,))
    print(f"Index ready: {destination}", flush=True)


def query_index(index, route, params):
    def value(name, default=''):
        return params.get(name, [default])[0]

    # Read-only connections: the web server never changes the index.
    with closing(sqlite3.connect(Path(index).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        if route == '/api/text':
            offset = max(0, int(value('offset', '0')))
            row = db.execute("SELECT substr(payload,?,?),length(payload) FROM records WHERE id=?",
                             (offset + 1, TEXT_CHUNK, int(value('id')))).fetchone()
            if row is None:
                raise ValueError('Unknown record')
            return {'text': row[0], 'next': offset + len(row[0]), 'more': offset + len(row[0]) < row[1]}
        page = max(0, int(value('page', '0')))
        if route == '/api/cids':
            rows = db.execute("SELECT c.cid,COUNT(r.id) count FROM conversations c JOIN records r ON r.cid=c.cid "
                              "WHERE instr(c.cid,?)>0 AND (?='' OR r.intent=?) "
                              "GROUP BY c.cid ORDER BY c.cid LIMIT ? OFFSET ?",
                              (value('q'), value('intent'), value('intent'), PAGE_SIZE + 1,
                               page * PAGE_SIZE)).fetchall()
        elif route == '/api/records':
            rows = db.execute("SELECT id,substr(source,1,200) source,substr(record_id,1,200) record_id,"
                              "substr(intent,1,500) intent,substr(agent,1,500) agent,"
                              "substr(function_name,1,500) function_name,substr(run_id,1,200) run_id,"
                              "CASE WHEN length(payload)<=? THEN payload ELSE NULL END preview "
                              "FROM records WHERE cid=? AND (?='' OR source=?) AND (?='' OR intent=?) "
                              "ORDER BY id LIMIT ? OFFSET ?",
                              (PREVIEW_LIMIT, value('cid'), value('source'), value('source'),
                               value('intent'), value('intent'), PAGE_SIZE + 1, page * PAGE_SIZE)).fetchall()
            result = []
            for row in rows[:PAGE_SIZE]:
                item = dict(row)
                preview = item.pop('preview')
                item['preview'] = json.loads(preview) if preview is not None else None
                result.append(item)
            counts = dict(db.execute("SELECT source,count(*) FROM records WHERE cid=? AND (?='' OR intent=?) GROUP BY source",
                                     (value('cid'), value('intent'), value('intent'))).fetchall())
            return {'rows': result, 'more': len(rows) > PAGE_SIZE, 'counts': counts}
        elif route == '/api/intents':
            rows = db.execute("SELECT DISTINCT intent FROM records WHERE intent<>'' ORDER BY intent").fetchall()
            return {'rows': [row[0] for row in rows]}
        elif route == '/api/overview':
            # Feedback counts use the same convention as the original viewer:
            # non-empty negative feedback means negative; otherwise positive.
            counts = db.execute("SELECT value FROM metadata WHERE key='feedback_overview'").fetchone()
            return json.loads(counts[0]) if counts else {}
        else:
            raise ValueError('Unknown endpoint')
        return {'rows': [dict(row) for row in rows[:PAGE_SIZE]], 'more': len(rows) > PAGE_SIZE}


def handler(index):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            # Reject other hostnames and cross-origin access to local data.
            expected = f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') != expected or self.headers.get('Origin') not in (None, 'http://' + expected):
                self.send_error(403)
                return
            request = urlparse(self.path)
            try:
                if request.path == '/':
                    body, content_type = viewer_page().encode(), 'text/html; charset=utf-8'
                else:
                    body = json.dumps(query_index(index, request.path, parse_qs(request.query))).encode()
                    content_type = 'application/json'
            except (ValueError, sqlite3.Error):
                self.send_error(400, 'Invalid viewer request')
                return
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Frame-Options', 'DENY')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', type=Path)
    parser.add_argument('--index', type=Path, default=Path('output/interaction_viewer.sqlite3'))
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    build_index(args.csv, args.index)
    prepare_overview(args.index)
    with ThreadingHTTPServer(('127.0.0.1', args.port), handler(args.index)) as server:
        print(f'Open http://127.0.0.1:{server.server_port} — Ctrl+C to stop', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
