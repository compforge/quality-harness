"""三表持久化：parquet（需 `[trace-corpus]` extra 的 pyarrow）优先，缺则回退 jsonl。

corpus 三表是机器查询面（百万行级），parquet + duckdb 是标准答案；但 parquet 走可选 extra
不给其它 harness 增重，缺 pyarrow 时回退 jsonl（功能不减、只是大表查询面退化）。写盘前把
异构行（各 kind facts 列不同）补成并集列、缺位填 None，保证 parquet schema 稳定、jsonl 一致。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from trace_harness.corpus.tables import CorpusTables

_TABLES = ("traces", "facts", "findings")


def _have_pyarrow() -> bool:
    return importlib.util.find_spec("pyarrow") is not None


def _union_rows(rows: list[dict]) -> list[dict]:
    """补成并集列、缺位填 None——异构 facts 行的 schema 稳定化。"""
    cols: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                cols.append(k)
    return [{c: r.get(c) for c in cols} for r in rows]


def _write_jsonl(rows: list[dict], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def _write_parquet(rows: list[dict], path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    pq.write_table(pa.Table.from_pylist(rows or [{}]), path)


def write_tables(tables: CorpusTables, out_dir: str | Path) -> str:
    """写三表 + meta.json（含 metric_cols / 格式）。返回实际格式（"parquet"|"jsonl"）。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    fmt = "parquet" if _have_pyarrow() else "jsonl"
    for name in _TABLES:
        rows = _union_rows(getattr(tables, name))
        if fmt == "parquet":
            _write_parquet(rows, out / f"{name}.parquet")
        else:
            _write_jsonl(rows, out / f"{name}.jsonl")
    (out / "meta.json").write_text(
        json.dumps({"format": fmt, "metric_cols": sorted(tables.metric_cols)}, ensure_ascii=False),
        encoding="utf-8",
    )
    return fmt


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _read_parquet(path: Path) -> list[dict]:
    import pyarrow.parquet as pq

    return pq.read_table(path).to_pylist()


def read_tables(in_dir: str | Path) -> CorpusTables:
    """读回三表（parquet 或 jsonl）+ metric_cols（meta.json）——供 diff 跨 run 对比。"""
    d = Path(in_dir)
    meta = (
        json.loads((d / "meta.json").read_text(encoding="utf-8"))
        if (d / "meta.json").exists()
        else {}
    )
    t = CorpusTables(metric_cols=set(meta.get("metric_cols", [])))
    for name in _TABLES:
        pq_path, jl_path = d / f"{name}.parquet", d / f"{name}.jsonl"
        if pq_path.exists():
            setattr(t, name, _read_parquet(pq_path))
        elif jl_path.exists():
            setattr(t, name, _read_jsonl(jl_path))
    return t
