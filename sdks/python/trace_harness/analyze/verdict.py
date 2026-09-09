"""Project a trace batch run into the cross-harness ``verdict.json``（spec/verdict-schema.yaml）。

schema/序列化在 `common.verdict`；本模块只保留 trace 的投影。语义边界（设计文档决策记录）：
**Finding 是发现（finding），verdict 是对照预期的判定**——nightly 语料里找出 10 个错误签名是
分析的成功，不是 run 的 fail，所以 Finding 永不直接变 verdict。可判定的是 experiment 里
**显式声明的 gate**（对三表/算子结果的断言，如"对基线无新增错误签名"），每条 gate →
``checks[]`` 一行——与 perf 的 SLO check 同一种判定单元（run 级断言门，cases 恒空）。

status 沿用 perf 的记录门诚实原则：没声明 gate → ``skipped``（分析产物是交付物，但没有任何
判定被执行，不能读成 green）；声明了按 fail > pass > skipped rollup。
"""

from __future__ import annotations

from pathlib import Path

from harness_common import verdict as _v

from trace_harness.corpus.operators import fleet_outliers
from trace_harness.corpus.tables import CorpusTables

# 已知 gate 键 → 求值函数名；未知键在 evaluate_gates 处 fail fast，避免拼错的 gate 静默不判
_KNOWN_GATES = ("max_error_findings", "no_new_signatures", "fleet_outlier_ratio_max")


def evaluate_gates(
    tables: CorpusTables,
    gates: dict,
    diff_result: dict | None = None,
) -> list[_v.CheckVerdict]:
    """声明的 gates → CheckVerdict 列表。逐条求值：

    - ``max_error_findings: N``        findings 表 severity=="error" 的行数 ≤ N
    - ``no_new_signatures: true``   diff 对基线无新增错误签名（需要 ``diff:`` 基线；缺 → skipped）
    - ``fleet_outlier_ratio_max: X``舰队离群最大倍数 ≤ X（无离群 → observed 0 → pass）
    """
    unknown = set(gates) - set(_KNOWN_GATES)
    if unknown:
        raise ValueError(f"unknown gate(s) {sorted(unknown)}; known: {list(_KNOWN_GATES)}")

    checks: list[_v.CheckVerdict] = []
    if "max_error_findings" in gates:
        limit = int(gates["max_error_findings"])
        observed = sum(1 for m in tables.findings if m.get("severity") == "error")
        checks.append(
            _v.CheckVerdict(
                name=f"error_findings <= {limit}",
                status="pass" if observed <= limit else "fail",
                metric="error_findings",
                observed=float(observed),
            )
        )
    if gates.get("no_new_signatures"):
        if diff_result is None:
            # 声明了却没给 diff 基线 → 没被验证过，按记录门原则记 skipped 而非 pass
            checks.append(
                _v.CheckVerdict(
                    name="no_new_signatures",
                    status="skipped",
                    metric="new_signatures",
                    reason="gate declared but experiment has no diff baseline",
                )
            )
        else:
            observed = len(diff_result.get("sig_added", []))
            checks.append(
                _v.CheckVerdict(
                    name="no_new_signatures",
                    status="pass" if observed == 0 else "fail",
                    metric="new_signatures",
                    observed=float(observed),
                )
            )
    if "fleet_outlier_ratio_max" in gates:
        limit = float(gates["fleet_outlier_ratio_max"])
        outliers = fleet_outliers(tables)
        observed = max((o["ratio"] for o in outliers), default=0.0)
        checks.append(
            _v.CheckVerdict(
                name=f"fleet_outlier_ratio <= {limit}",
                status="pass" if observed <= limit else "fail",
                metric="fleet_outlier_ratio",
                observed=observed,
            )
        )
    return checks


def _build(
    scope: str,
    run_id: str,
    tables: CorpusTables,
    gates: dict,
    diff_result: dict | None,
) -> _v.RunVerdict:
    checks = evaluate_gates(tables, gates, diff_result)
    n_fail = sum(1 for c in checks if c.status == "fail")
    n_pass = sum(1 for c in checks if c.status == "pass")
    if not checks:
        status, reason = "skipped", "no gates declared (analysis artifacts only)"
    elif n_fail:
        status = "fail"
        first = next(c for c in checks if c.status == "fail")
        reason = f"{n_fail} gate(s) failed; first — {first.name} (observed {first.observed})"
    elif n_pass:
        status, reason = "pass", None
    else:
        status, reason = "skipped", "gates declared but none could evaluate"
    return _v.build_run_verdict(
        "trace",
        scope,
        run_id,
        [],
        checks=checks,
        status=status,
        reason=reason,
        artifact_paths={"report": "report.html"},
    )


def write_verdict(
    run_dir: str | Path,
    scope: str,
    run_id: str,
    tables: CorpusTables,
    gates: dict | None = None,
    diff_result: dict | None = None,
) -> Path:
    """Write ``verdict.json`` into ``run_dir``. Returns its path."""
    return _v.write_verdict(run_dir, _build(scope, run_id, tables, gates or {}, diff_result))
