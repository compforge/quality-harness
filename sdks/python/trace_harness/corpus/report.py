"""corpus 报告——走 common.report_kit IR（与单 trace html、eval/perf 报告同壳）。

把三表 + 算子结果映射成 Report：错误签名 Pareto / 舰队离群表 / （可选）diff 龙卷风。
report_kit 业务无关，本模块负责把 corpus 结果 pivot 成它的 Section/Table。
"""

from __future__ import annotations

from pathlib import Path

from harness_common.report_kit import Report, Section, Table, render_html

from trace_harness.corpus.operators import error_signatures, fleet_outliers, pattern_rates
from trace_harness.corpus.tables import CorpusTables


def build_report(name: str, tables: CorpusTables, diff_result: dict | None = None) -> Report:
    sections: list[Section] = []

    sigs = error_signatures(tables)
    if sigs:
        rows = [[s["sample"][:80], str(s["count"]), str(s["n_traces"])] for s in sigs]
        sections.append(
            Section(
                "错误签名",
                [Table(["签名样例", "次数", "trace 数"], rows, sort_default=(1, "desc"))],
            )
        )

    fo = fleet_outliers(tables)
    if fo:
        rows = [
            [
                o["kind"],
                o["name"],
                o["metric"],
                f"{o['value']:.0f}",
                f"{o['median']:.0f}",
                f"{o['ratio']}×",
                o["trace_id"],
            ]
            for o in fo
        ]
        sections.append(
            Section(
                "舰队离群",
                [
                    Table(
                        ["kind", "name", "metric", "值", "中位", "倍数", "trace"],
                        rows,
                        sort_default=(5, "desc"),
                    )
                ],
            )
        )

    rates = pattern_rates(tables)
    if rates:
        rows = [
            [r["pattern"], r["name"], str(r["count"]), str(r["n_traces"]), f"{r['trace_pct']}%"]
            for r in rates
        ]
        sections.append(
            Section(
                "行为模式（出现率）",
                [Table(["pattern", "对象", "次数", "trace 数", "trace 占比"], rows)],
            )
        )

    if diff_result:
        sections.append(_diff_section(diff_result))

    n_err_traces = sum(1 for t in tables.traces if t["n_errors"] > 0)
    meta = [
        ("traces", str(len(tables.traces))),
        ("含错 trace", str(n_err_traces)),
        ("nodes", str(len(tables.facts))),
        ("findings", str(len(tables.findings))),
    ]
    return Report(title=f"corpus · {name}", meta=meta, sections=sections)


def _diff_section(d: dict) -> Section:
    rows = []
    for s in d.get("sig_added", []):
        rows.append(["+ 新增", s["sample"][:60], str(s["count"])])
    for s in d.get("sig_removed", []):
        rows.append(["- 消失", s["sample"][:60], str(s["count"])])
    for s in d.get("sig_changed", []):
        rows.append(["~ 变化", s["signature"][:60], f"{s['count_a']}→{s['count_b']}"])
    for m in d.get("metric_shifts", []):
        rows.append(
            [
                f"漂移 {m['kind']}/{m['name']}",
                m["metric"],
                f"{m['median_a']:.0f}→{m['median_b']:.0f}（{m['ratio']}×）",
            ]
        )
    return Section("diff（vs 基线 run）", [Table(["变化", "对象", "详情"], rows)])


def write_report(
    name: str, tables: CorpusTables, out_dir: str | Path, diff_result: dict | None = None
) -> Path:
    report = build_report(name, tables, diff_result=diff_result)
    path = Path(out_dir) / "report.html"
    path.write_text(render_html(report), encoding="utf-8")
    return path
