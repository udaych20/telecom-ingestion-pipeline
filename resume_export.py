"""Durable CID checkpoints for incremental CSV exports."""

import csv
import json
import os
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path


class ResumeExport:
    def __init__(self, paths, resume, scope):
        self.paths = paths
        self.path = paths[0].with_suffix('.checkpoint.sqlite3')
        if resume and any(not p.exists() for p in paths):
            raise ValueError('Resume requires all existing classification CSVs, including the clarification CSV')
        self.db = sqlite3.connect(self.path)
        self.db.execute('CREATE TABLE IF NOT EXISTS completed (cid TEXT PRIMARY KEY, rows INTEGER)')
        self.db.execute('CREATE TABLE IF NOT EXISTS config (value TEXT)')
        scope = json.dumps(scope, sort_keys=True, default=str)
        if not resume:
            self.db.execute('DELETE FROM completed')
            self.db.execute('DELETE FROM config')
        previous = self.db.execute('SELECT value FROM config').fetchone()
        if previous and previous[0] != scope:
            self.db.close()
            raise ValueError('Resume configuration differs from the original export; restore the original source, time range and output settings')
        if not previous:
            self.db.execute('INSERT INTO config VALUES (?)', (scope,))
        if not resume:
            self.db.commit()
        if resume:
            counts = Counter()
            for row in self.read_rows():
                cid = row.get('conversation_id', '')
                if not cid:
                    raise ValueError('Cannot safely resume a CSV row without conversation_id')
                counts[cid] += 1
            if not previous:
                # Compatibility with exports stopped cleanly before checkpoints existed.
                self.db.executemany('INSERT INTO completed VALUES (?, ?)', counts.items())
                self.db.commit()
            completed = dict(self.db.execute('SELECT cid, rows FROM completed'))
            if any(counts[cid] != count for cid, count in completed.items()):
                raise ValueError('Saved CSV rows do not match the checkpoint; restore the matching output files')
            if set(counts) - set(completed):
                # A process may stop after writing rows but before committing the CID.
                for path in paths:
                    with path.open(encoding='utf-8-sig', newline='') as source:
                        reader = csv.DictReader(source)
                        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8-sig', newline='',
                                                         dir=path.parent, delete=False) as target:
                            temporary = Path(target.name)
                            writer = csv.DictWriter(target, fieldnames=reader.fieldnames)
                            writer.writeheader()
                            writer.writerows(row for row in reader if row['conversation_id'] in completed)
                            target.flush()
                            os.fsync(target.fileno())
                    temporary.replace(path)
        self.completed = {cid for cid, in self.db.execute('SELECT cid FROM completed')}
        self.db.commit()

    def read_rows(self):
        for path in self.paths:
            with path.open(encoding='utf-8-sig', newline='') as source:
                for row in csv.DictReader(source, strict=True):
                    if None in row or any(value is None for value in row.values()):
                        raise ValueError(f'Malformed CSV: {path}; restore a complete backup before resuming')
                    yield row

    def mark(self, cid, count):
        self.db.execute('INSERT OR REPLACE INTO completed VALUES (?, ?)', (cid, count))
        self.db.commit()
        self.completed.add(cid)

    def close(self):
        self.db.close()
