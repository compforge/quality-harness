"""experiment 驱动：一份 yaml = 一个 experiment，产物落 runs/<name>/<run-id>/。

2b 的 source 是离线 jaeger 文件（jaeger_dir / jaeger_files）；opensearch/driver 等在线 source
随后续。流程：解析 source → 逐文件 build_context + diagnose → build_tables → 写三表 + 报告
+ verdict.json（统一判定出口；可选 `gates:` 声明才判定，否则 status=skipped）。
可选 `diff: <基线 run dir>` 读回基线三表、跑 diff、并入报告与 gate 求值。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yaml
from harness_common.run import run_dir_for

from trace_harness.analyze.diagnose import diagnose
from trace_harness.analyze.verdict import write_verdict
from trace_harness.corpus.operators import diff_runs
from trace_harness.corpus.report import write_report
from trace_harness.corpus.store import read_tables, write_tables
from trace_harness.corpus.tables import build_tables
from trace_harness.ingest.load import build_context


def _resolve_files(source: dict, base: Path) -> list[Path]:
    def _abs(p: str) -> Path:
        q = Path(p)
        return q if q.is_absolute() else base / q

    if "jaeger_dir" in source:
        return sorted(_abs(source["jaeger_dir"]).glob("*.jsonl"))
    if "jaeger_files" in source:
        return [_abs(f) for f in source["jaeger_files"]]
    raise ValueError("source 需要 jaeger_dir 或 jaeger_files（2b 仅离线 jaeger）")


def run_experiment(
    exp_path: str | Path, runs_dir: str | Path = "runs", run_id: str | None = None
) -> Path:
    exp_path = Path(exp_path)
    cfg = yaml.safe_load(exp_path.read_text(encoding="utf-8")) or {}
    name = cfg.get("name") or exp_path.stem
    base = exp_path.parent
    files = _resolve_files(cfg.get("source") or {}, base)
    probes = bool((cfg.get("diagnose") or {}).get("probes", False))

    items = []
    for f in files:
        ctx = build_context(f)
        items.append((ctx, diagnose(ctx, probes=probes)))
    tables = build_tables(items)

    diff_result = None
    if cfg.get("diff"):
        baseline = read_tables(_resolve_baseline(cfg["diff"], base))
        diff_result = diff_runs(baseline, tables)

    run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")  # 每个 run 一个时间戳目录
    rd = run_dir_for(runs_dir, name, run_id)
    rd.mkdir(parents=True, exist_ok=True)
    write_tables(tables, rd)
    write_report(name, tables, rd, diff_result=diff_result)
    # 统一判定出口：verdict.json 必产（契约：消费方 glob runs/**/verdict.json）。
    # Finding 是发现、不是判定——只有显式声明的 gates 才进 checks[]；没声明 → skipped。
    write_verdict(rd, name, run_id, tables, gates=cfg.get("gates") or {}, diff_result=diff_result)
    return rd


def _resolve_baseline(p: str, base: Path) -> Path:
    q = Path(p)
    return q if q.is_absolute() else base / q
