# Large interaction CSV viewer

For exports that crash the standalone HTML viewer, run this from the repository:

```powershell
python large_interaction_viewer.py output/interactions.csv
```

Wait for `Index ready`, then open **http://127.0.0.1:8765** in your browser.
Keep the terminal running; press Ctrl+C to stop. No additional Python packages
are needed. The server listens only on the local machine and does not upload data.

The first run streams the completed CSV into `output/interaction_viewer.sqlite3`.
Allow substantial free disk space for the index and SQLite working files; the
index can be larger than the original CSV. Indexing holds one CSV record at a
time rather than the whole file; an exceptionally large individual row can still
require significant memory. Progress is printed every 1,000 source records.
Do not index an export that is still being written.

Later runs reuse the index if the input path, size and modification time match.
If the CSV changes, or indexing was interrupted, use a new index filename:

```powershell
python large_interaction_viewer.py output/interactions.csv --index output/viewer_v2.sqlite3
```

The original CSV is never changed. Index files contain the same sensitive data
as the export; protect them accordingly. The server rejects non-local Host headers
and cross-origin requests but has no user authentication; other programs on the
same computer can access it while running.

Search is by literal CID text. Conversations and source records are paginated,
20 at a time. The viewer reuses the original HTML's styling and chat, agent,
tool and feedback renderers. Source filters apply to the selected conversation;
source counts cover the whole conversation, not just the loaded page.
Feedback summary cards cover the whole export and are cached on disk at startup.
Existing indexes are reused; the first updated run prepares the feedback summary.

Records up to 64,000 serialized characters are rendered as formatted cards.
Larger records show a summary and a complete, chunked CSV-row view with Previous
and Next buttons. Each text chunk is 32,000 characters and replaces the previous
chunk to keep browser memory bounded. No stored values are truncated.

Keep `interactions-viewer.html` and `large-viewer.js` beside the Python script;
they supply the shared design and large-file controls. Search remains CID-only,
not full-text search. This viewer does not modify or correct intent classifications.
