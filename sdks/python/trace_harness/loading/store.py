"""Persistent evidence cache, separate from run-local computed results."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from trace_harness.ingest.sources.jaeger_file import normalize_es_doc
from trace_harness.model.span import NormSpan


class EvidenceStore:
    def __init__(self, path: Path):
        path.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path / "index.sqlite")
        self.db.execute("CREATE TABLE IF NOT EXISTS objects(key TEXT PRIMARY KEY, value TEXT)")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS fields(trace TEXT, span TEXT, name TEXT, value TEXT, "
            "PRIMARY KEY(trace,span,name))"
        )

    def get(self, key):
        row = self.db.execute("SELECT value FROM objects WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO objects VALUES(?,?)", (key, json.dumps(value)))

    def read(self, trace_id: str, span_id: str, names: tuple[str, ...] | None):
        full = self.get(f"full:{trace_id}:{span_id}")
        if full is not None:
            return normalize_es_doc(full)
        if names is None:
            return None
        rows = dict(
            self.db.execute(
                "SELECT name,value FROM fields WHERE trace=? AND span=?", (trace_id, span_id)
            )
        )
        if not set(names).issubset(rows):
            return None
        base = self.get(f"base:{trace_id}:{span_id}")
        if base is None:
            return None
        base["tags"] = []
        for name in names:
            value = json.loads(rows[name])
            if value["present"]:
                base["tags"].append({"key": name, "value": value["value"]})
        return normalize_es_doc(base)

    def save(self, trace_id: str, span: NormSpan, names: tuple[str, ...] | None):
        if names is None:
            self.put(f"full:{trace_id}:{span.span_id}", span.raw)
            return
        self.put(
            f"base:{trace_id}:{span.span_id}", {k: v for k, v in span.raw.items() if k != "tags"}
        )
        with self.db:
            self.db.executemany(
                "INSERT OR REPLACE INTO fields VALUES(?,?,?,?)",
                [
                    (
                        trace_id,
                        span.span_id,
                        name,
                        json.dumps({"present": name in span.attrs, "value": span.attrs.get(name)}),
                    )
                    for name in names
                ],
            )

    def close(self):
        self.db.close()
