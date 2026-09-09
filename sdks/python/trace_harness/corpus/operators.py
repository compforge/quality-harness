"""三个 corpus 算子（v0）——纯列运算，不回头碰 raw、不认识 kind 业务语义。

- error_signature：错误文案归一聚类（"aigw · timeout ×N"），把同类错误收成签名
- fleet_outlier：同 (kind,name) 跨 trace 建中位基线，找比舰队中位慢 3× 的节点（单 trace 内看不见）
- diff：两个 run 对比（签名增减 + 分位漂移），回归检测
- pattern_rates：行为模式（pattern:*）跨 trace 出现率——单条是嫌疑，聚合后才可行动（§3.7）

metric 口径：fleet/diff 只对 `tables.metric_cols`（来自 spec.metrics）建基线，与 1b outlier 一致。
"""

from __future__ import annotations

import re
from collections import defaultdict

from trace_harness.corpus.tables import CorpusTables

# 把数字 / 十六进制 id / uuid 归一成占位符，让"504"和"503"、不同 trace_id 聚到同一签名
_TOKEN = re.compile(r"\b(0x[0-9a-f]+|[0-9a-f-]{12,}|\d[\d.]*)\b", re.IGNORECASE)


def _median(xs: list[float]) -> float:
    xs = sorted(xs)
    n = len(xs)
    mid = n // 2
    return xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2


def _normalize(text: str) -> str:
    return _TOKEN.sub("·", text.strip().lower())


def error_signatures(tables: CorpusTables, sources: tuple[str, ...] = ("error",)) -> list[dict]:
    """findings 表里 source 命中 sources 的，按归一文案聚类。按出现次数降序。"""
    buckets: dict[str, dict] = defaultdict(lambda: {"count": 0, "traces": set(), "sample": ""})
    for row in tables.findings:
        if not any(str(row["source"]).startswith(s) for s in sources):
            continue
        text = row["note"] or row["source"]
        b = buckets[_normalize(text)]
        b["count"] += 1
        b["traces"].add(row["trace_id"])
        if not b["sample"]:
            b["sample"] = text
    out = [
        {"signature": sig, "count": b["count"], "n_traces": len(b["traces"]), "sample": b["sample"]}
        for sig, b in buckets.items()
    ]
    out.sort(key=lambda x: -x["count"])
    return out


def _numeric(v: object) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def fleet_outliers(tables: CorpusTables, ratio: float = 3.0, min_peers: int = 5) -> list[dict]:
    """同 (kind,name) 跨 trace 对每个 metric 列建中位基线，标 value ≥ ratio×中位 的节点。"""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in tables.facts:
        groups[(row["kind"], row["name"])].append(row)
    out: list[dict] = []
    for (kind, name), rows in groups.items():
        if len(rows) < min_peers:
            continue
        for metric in tables.metric_cols:
            vals = [(r, r[metric]) for r in rows if _numeric(r.get(metric))]
            if len(vals) < min_peers:
                continue
            med = _median([v for _, v in vals])
            if med <= 0:
                continue
            for r, v in vals:
                if v >= ratio * med:
                    out.append(
                        {
                            "kind": kind,
                            "name": name,
                            "metric": metric,
                            "trace_id": r["trace_id"],
                            "node_id": r["node_id"],
                            "value": v,
                            "median": med,
                            "ratio": round(v / med, 2),
                        }
                    )
    out.sort(key=lambda x: -x["ratio"])
    return out


def _medians(tables: CorpusTables) -> dict[tuple[str, str, str], float]:
    """每个 (kind,name,metric) 的跨 trace 中位——diff 的分位基线。"""
    groups: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for row in tables.facts:
        for metric in tables.metric_cols:
            if _numeric(row.get(metric)):
                groups[(row["kind"], row["name"], metric)].append(row[metric])
    return {k: _median(v) for k, v in groups.items() if v}


def diff_runs(a: CorpusTables, b: CorpusTables, ratio: float = 1.5) -> dict:
    """两个 run 对比：错误签名增/减/变 + (kind,name,metric) 中位漂移 ≥ ratio。"""
    sa = {s["signature"]: s for s in error_signatures(a)}
    sb = {s["signature"]: s for s in error_signatures(b)}
    sig_added = [sb[k] for k in sb if k not in sa]
    sig_removed = [sa[k] for k in sa if k not in sb]
    sig_changed = [
        {"signature": k, "count_a": sa[k]["count"], "count_b": sb[k]["count"]}
        for k in sa
        if k in sb and sa[k]["count"] != sb[k]["count"]
    ]
    ma, mb = _medians(a), _medians(b)
    shifts = []
    for key in set(ma) & set(mb):
        va, vb = ma[key], mb[key]
        if va > 0 and vb > 0 and (vb / va >= ratio or va / vb >= ratio):
            kind, name, metric = key
            shifts.append(
                {
                    "kind": kind,
                    "name": name,
                    "metric": metric,
                    "median_a": va,
                    "median_b": vb,
                    "ratio": round(vb / va, 2),
                }
            )
    shifts.sort(key=lambda x: -max(x["ratio"], 1 / x["ratio"]))
    return {
        "sig_added": sig_added,
        "sig_removed": sig_removed,
        "sig_changed": sig_changed,
        "metric_shifts": shifts,
    }


def _pctl(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def contrast(
    tables: CorpusTables,
    group_by: tuple[str, ...] = ("kind", "name"),
    split: str = "has_error",
    metric_cols: set[str] | None = None,
) -> list[dict]:
    """把 facts 表按 group_by 分组、组内按 `split` 列分桶，逐 metric 列对比分布。

    这是 fleet_outlier 的兄弟——fleet 找「同类里离群的个体」，contrast 找「两个子总体
    （失败 vs 成功 / run A vs B）某列分布是否分化」。直接产出「失败组 in_chars 反而更小
    → 与输入大小无关」这种结论。split 缺省 has_error（失败 vs 成功），也可传任意分类列。
    """
    cols = metric_cols or tables.metric_cols
    groups: dict[tuple, dict] = defaultdict(lambda: defaultdict(list))
    for r in tables.facts:
        gkey = tuple(r.get(k) for k in group_by)
        groups[gkey][r.get(split)].append(r)
    out: list[dict] = []
    for gkey, by_split in groups.items():
        if len(by_split) < 2:  # 没有可对比的两桶
            continue
        for metric in cols:
            buckets: dict[str, dict] = {}
            for sval, rows in by_split.items():
                vals = [r[metric] for r in rows if _numeric(r.get(metric))]
                if vals:
                    buckets[str(sval)] = {
                        "n": len(vals),
                        "p50": round(_median(vals), 2),
                        "p90": round(_pctl(vals, 0.9), 2),
                        "max": max(vals),
                    }
            if len(buckets) < 2:
                continue
            out.append(
                {
                    "group": dict(zip(group_by, gkey, strict=False)),
                    "metric": metric,
                    "split": split,
                    "buckets": buckets,
                }
            )
    return out


def pattern_rates(tables: CorpusTables) -> list[dict]:
    """行为模式出现率：findings 表里 `pattern:*` 的行，join facts 拿对象名（tool/name），
    按 (pattern, 对象) 聚合 → 次数 / 命中 trace 数 / trace 占比。出现率是 pattern 从
    「单条嫌疑」变「可行动结论」的判据（如"fail 的 trace 里 78% 命中 tool_churn"）。"""
    facts_by = {(r["trace_id"], r["node_id"]): r for r in tables.facts}
    buckets: dict[tuple[str, str], dict] = defaultdict(lambda: {"count": 0, "traces": set()})
    for row in tables.findings:
        src_ = str(row["source"])
        if not src_.startswith("pattern:"):
            continue
        f = facts_by.get((row["trace_id"], row["node_id"]), {})
        key = (src_, str(f.get("tool") or f.get("name") or "?"))
        b = buckets[key]
        b["count"] += 1
        b["traces"].add(row["trace_id"])
    total = len(tables.traces) or 1
    out = [
        {
            "pattern": pat,
            "name": name,
            "count": b["count"],
            "n_traces": len(b["traces"]),
            "trace_pct": round(100 * len(b["traces"]) / total, 1),
        }
        for (pat, name), b in buckets.items()
    ]
    out.sort(key=lambda x: (-x["n_traces"], -x["count"]))
    return out
