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
PAGE = """<!doctype html><meta charset="utf-8"><title>NORA Large Interaction Viewer</title>
<style>body{font:16px system-ui;margin:24px;background:#f4f7fa;color:#152b40}
button,input{padding:8px;margin:4px}article{background:white;padding:16px;margin:12px 0;border:1px solid #ccd}
pre{white-space:pre-wrap;overflow-wrap:anywhere}#layout{display:grid;grid-template-columns:320px 1fr;gap:20px}
#cids button{display:block;overflow-wrap:anywhere;width:100%;text-align:left}#status{color:#934}
</style><h1>NORA Large Interaction Viewer</h1><p>Local disk-backed view · original CSV is unchanged</p>
<p id="status"></p><div id="layout"><aside><input id="search" placeholder="Search CID (literal text)">
<button id="find">Search</button><div id="cids"></div><button id="prevCid">Previous</button>
<button id="nextCid">Next</button></aside><main><h2 id="selected">Select a conversation</h2>
<p id="info"></p><button id="prevRow">Previous records</button><button id="nextRow">Next records</button>
<div id="records"></div></main></div>
<script>
let cidPage=0,rowPage=0,selected='',query='';
const el=id=>document.getElementById(id);
async function api(path){const r=await fetch(path);if(!r.ok)throw Error(await r.text());return r.json()}
function guarded(fn){return async()=>{try{el('status').textContent='';await fn()}catch(e){el('status').textContent=e.message}}}
async function conversations(){
 const data=await api('/api/cids?page='+cidPage+'&q='+encodeURIComponent(query));
 el('cids').replaceChildren();for(const row of data.rows){const b=document.createElement('button');
 b.textContent=row.cid+' ('+row.count+' records)';b.onclick=guarded(async()=>{selected=row.cid;rowPage=0;await records()});el('cids').append(b)}
 el('prevCid').disabled=cidPage===0;el('nextCid').disabled=!data.more;
}
async function records(){
 const wanted=selected,page=rowPage;
 const data=await api('/api/records?cid='+encodeURIComponent(wanted)+'&page='+page);
 if(selected!==wanted||rowPage!==page)return;
 el('selected').textContent=wanted;el('info').textContent='Records '+(page*20+1)+'–'+(page*20+data.rows.length);
 el('records').replaceChildren();el('prevRow').disabled=page===0;el('nextRow').disabled=!data.more;
 for(const row of data.rows){const card=document.createElement('article');const title=document.createElement('h3');
 title.textContent=row.source+' · '+row.record_id;card.append(title);
 const summary=document.createElement('pre');summary.textContent='Intent: '+row.intent+'\\nAgent: '+row.agent+'\\nFunction: '+row.function_name+'\\nRun: '+row.run_id;card.append(summary);
 const note=document.createElement('p');note.textContent='Long summary fields are previews. Open CSV row for complete values.';card.append(note);
 const button=document.createElement('button');button.textContent='Open CSV row (input/output and full JSON)';card.append(button);
 const output=document.createElement('pre');card.append(output);let offset=0;
 button.onclick=guarded(async()=>{const part=await api('/api/text?id='+row.id+'&offset='+offset);
 output.textContent=part.text;offset=part.next;button.textContent=part.more?'Next text chunk':'End of record';button.disabled=!part.more});
 el('records').append(card);}
}
el('find').onclick=guarded(async()=>{query=el('search').value;cidPage=0;await conversations()});
el('prevCid').onclick=guarded(async()=>{cidPage--;await conversations()});
el('nextCid').onclick=guarded(async()=>{cidPage++;await conversations()});
el('prevRow').onclick=guarded(async()=>{if(selected){rowPage--;await records()}});
el('nextRow').onclick=guarded(async()=>{if(selected){rowPage++;await records()}});
guarded(conversations)();
</script>"""


def signature(path):
    stat = path.stat()
    return json.dumps([str(path.resolve()), stat.st_size, stat.st_mtime_ns])


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
            rows = db.execute("SELECT cid,count FROM conversations WHERE instr(cid,?)>0 ORDER BY cid LIMIT ? OFFSET ?",
                              (value('q'), PAGE_SIZE + 1, page * PAGE_SIZE)).fetchall()
        elif route == '/api/records':
            rows = db.execute("SELECT id,substr(source,1,200) source,substr(record_id,1,200) record_id,"
                              "substr(intent,1,500) intent,substr(agent,1,500) agent,"
                              "substr(function_name,1,500) function_name,substr(run_id,1,200) run_id "
                              "FROM records WHERE cid=? ORDER BY id LIMIT ? OFFSET ?",
                              (value('cid'), PAGE_SIZE + 1, page * PAGE_SIZE)).fetchall()
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
                    body, content_type = PAGE.encode(), 'text/html; charset=utf-8'
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
    with ThreadingHTTPServer(('127.0.0.1', args.port), handler(args.index)) as server:
        print(f'Open http://127.0.0.1:{server.server_port} — Ctrl+C to stop', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
