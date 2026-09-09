"""probe —— 判读层唯一有副作用的环节：把"该看的原文"落盘成文件，路径进 Finding note。

probe 不判定好坏——效果问题的判定函数在数据外部（人/模型读了才知道），机器能做的是
**备料**：把 model io 原文、错误原文写到 evidence_dir，让读它的人/模型直接看。故默认关
（`diagnose(ctx, probes=True)` 才触发），看树/纯判读零落盘。

落盘用"原文填指针"的指针回原文：facts.io_span / error_span_ids 存的是 span_id，这里经
ctx.raw_attr 回干净物理 span 的 attrs。
"""

from __future__ import annotations

import json
from pathlib import Path

from trace_harness.model.context import TraceContext
from trace_harness.model.node import Finding


def probe(ctx: TraceContext) -> list[Finding]:
    if ctx.evidence_dir is None:
        return []
    edir = Path(ctx.evidence_dir)
    out: list[Finding] = []
    for n in ctx.nodes:
        targets: list[tuple[str, str]] = []
        if n.facts.get("io_span"):
            targets.append(("io", str(n.facts["io_span"])))
        for esid in n.error_span_ids:
            targets.append(("error", esid))
        if not targets:
            continue
        edir.mkdir(parents=True, exist_ok=True)
        for tag, sid in targets:
            fp = edir / f"{n.node_id}.{tag}.json"
            fp.write_text(
                json.dumps(ctx.raw_attr(sid), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            out.append(Finding(n.node_id, f"probe:{tag}", "info", note=str(fp)))
    return out
