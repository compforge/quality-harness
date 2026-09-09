"""Helpers for importers that turn an external QA dataset into a canonical CaseSet.

The generic, dataset-agnostic bits that every importer otherwise re-implements:
- ``slug`` — a filesystem-safe, collision-resistant doc filename from a title;
- ``dump_evalset`` — write the canonical CaseSet asset consumed by every harness.

The *corpus manifest* shape (how a SUT lists its sources) is intentionally
NOT here — eval_harness keeps "corpus = a name" and leaves source resolution to the
consumer's provisioner, so the manifest format is a consumer concern.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import yaml
from spec_case.model import case_to_raw

from eval_harness.model.evalset import EvalSet
from eval_harness.model.evalset import dump_cases_yaml as dump_cases_yaml  # re-export

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slug(title: str, *, max_len: int = 60) -> str:
    """Filesystem-safe slug + a short content hash for uniqueness.

    Two titles that collapse to the same base keep distinct filenames (the hash
    differs), and an empty/all-symbol title still yields a stable name.
    """
    base = _NON_ALNUM.sub("-", title.lower()).strip("-")[:max_len].strip("-")
    h = hashlib.sha1(title.encode()).hexdigest()[:6]  # noqa: S324 — filename uniqueness only
    return f"{base}-{h}" if base else h


def cached_download(url: str, *, cache_dir: str | Path | None = None) -> Path:
    """Download ``url`` once, keyed by URL under a cache dir (default the OS temp dir), and
    return the local path. Importers compose this instead of each re-implementing
    urllib + caching, so re-running doesn't re-download the source dataset."""
    cache = Path(cache_dir) if cache_dir else Path(tempfile.gettempdir())
    cache.mkdir(parents=True, exist_ok=True)
    local = cache / f"ingest-{slug(url)}"
    if not local.exists() or local.stat().st_size == 0:
        urllib.request.urlretrieve(url, local)  # noqa: S310 — caller supplies a trusted dataset URL
    return local


def fetch_json(url: str, *, cache_dir: str | Path | None = None) -> Any:
    """``cached_download`` + parse as JSON (the common dataset shape)."""
    return json.loads(cached_download(url, cache_dir=cache_dir).read_text(encoding="utf-8"))


def dump_evalset(evalset: EvalSet, out_dir: str | Path) -> Path:
    """Write a canonical CaseSet under ``out_dir``.

    Inline source ``content`` is materialised into ``docs/`` and rewritten to a ``uri`` —
    so the on-disk evalset references files, while an importer can build in memory. This
    is the single format an ``Importer`` produces and the config loader reads.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sources: list[dict] = []
    for s in evalset.sources:
        uri = s.uri
        if uri is None and s.content is not None:
            (out_dir / "docs").mkdir(exist_ok=True)
            (out_dir / "docs" / f"{slug(s.name)}.txt").write_text(s.content, encoding="utf-8")
            uri = f"docs/{slug(s.name)}.txt"
        rec: dict = {"name": s.name, "uri": uri}
        if s.meta:
            rec["meta"] = dict(s.meta)
        sources.append(rec)
    body: dict = {"caseset": evalset.caseset}
    if evalset.focus:
        body["focus"] = evalset.focus
    if evalset.facet_schema.facets:
        body["facets"] = {
            name: spec.model_dump(exclude_none=True, exclude_defaults=True)
            for name, spec in evalset.facet_schema.facets.items()
        }
    body["sources"] = sources
    body["cases"] = [case_to_raw(c) for c in evalset.cases]
    text = yaml.safe_dump(body, allow_unicode=True, sort_keys=False, width=4096)
    path = out_dir / "caseset.yaml"
    path.write_text(text, encoding="utf-8")
    return path


class Importer(ABC):
    """Maps an external/open dataset → a canonical CaseSet-shaped ``EvalSet``.

    The dataset→cases/sources mapping is SUT-independent, so a public-benchmark importer is
    reusable across consumers. Subclass and implement ``build``; ``write`` dumps it to disk.
    """

    @abstractmethod
    def build(self) -> EvalSet: ...

    def write(self, out_dir: str | Path) -> Path:
        return dump_evalset(self.build(), out_dir)
