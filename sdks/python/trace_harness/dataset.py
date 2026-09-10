"""Fixed evidence membership, independent of any analysis run."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Dataset:
    id: str
    source: str
    path: Path

    def members(self) -> Iterator[str]:
        """Stream fixed trace IDs; no tree or payload is retained by this iterator."""
        with (self.path / "members.jsonl").open() as stream:
            for line in stream:
                yield json.loads(line)

    def contains(self, trace_id: str) -> bool:
        db = sqlite3.connect(self.path / "index.sqlite")
        try:
            return (
                db.execute("SELECT 1 FROM members WHERE trace=?", (trace_id,)).fetchone()
                is not None
            )
        finally:
            db.close()

    @property
    def count(self) -> int:
        return json.loads((self.path / "manifest.json").read_text())["count"]

    @classmethod
    def load(cls, path: str | Path) -> Dataset:
        path = Path(path)
        manifest = json.loads((path / "manifest.json").read_text())
        return cls(manifest["id"], manifest["source"], path)
