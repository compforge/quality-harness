"""Report — measurement summary, Window/facet breakdown, and raw series.

Artifacts:
  - ``summary.csv`` + report.md §1: one row per Trial — measurement request-side
    stats (from Outcomes) beside resource-side stats (from Probes).
  - ``by_facet.csv`` + report.md §2: per Trial, the marginal pivot by each facet
    (slice p50/95/99 / err% / throughput by difficulty, by kb, …).
  - ``windows.csv`` + report.md §2b: request and resource summaries reduced on the
    same observed Window boundaries.
  - ``timeseries.csv``: long ``(trial, series, t, value)`` for plotting.

The md leads with the overall summary and flags the knee — the first Load level
per resource profile whose overall error rate crosses a threshold.

One config = one named *experiment* (the perf analogue of an eval_harness
experiment; its arms are the resources × load sweep). ``write_run`` lays each
run down under ``<runs_dir>/<experiment>/<run_id>/`` (report + csvs + config
snapshot + ``run.json``) and appends to the experiment's ``run.jsonl`` log, so
repeated runs accumulate instead of clobbering. ``write_report`` is the
lower-level artifact writer it builds on.
"""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import yaml
from harness_common.report_kit import (
    KV,
    Chart,
    Heading,
    LineSeries,
    Prose,
    Report,
    Section,
    Table,
    render_html,
)
from harness_common.run import run_dir_for

from perf_harness.metric import (
    LEGAL_STATS,
    MetricFamily,
    flatten,
    parse_ref,
    series_id,
    split_series,
)
from perf_harness.model import RequestStats, Run, Series, SloCheck, TrialRecord
from perf_harness.report.palette import family_color as _family_color
from perf_harness.runio import PerfReducer, write_timeseries_data
from perf_harness.slo import slo_aware_capacity

# service is constant across a report's rows (one Experiment = one Service) and is
# already in the title — so it's not a table column.
_KEY_COLS = ["resources", "model", "level"]
_STAT_COLS = ["n", "ok", "rps", "err%", "drop%", "p50_ms", "p95_ms", "p99_ms", "err_top"]

# header hover tooltips for the built-in request-side stat columns (metric/probe
# columns describe themselves from their MetricFamily — see _display_col_tips)
_STAT_TIPS = {
    "n": "sent requests (excludes client_saturated drops)",
    "ok": "requests judged OK (Workload.judge)",
    "rps": "throughput = sent ÷ steady window",
    "err%": "error rate — judged failures ÷ sent",
    "drop%": "open-loop client_saturated drops ÷ offered (not latency samples)",
    "p50_ms": "median request latency",
    "p95_ms": "95th-pct request latency",
    "p99_ms": "99th-pct request latency",
    "err_top": "most common error bucket (count)",
}

# drop rate above which a Trial's latency/throughput are not trustworthy (coordinated
# omission): the generator shed intended load, so percentiles understate reality.
_SATURATION_FLAG = 0.01


def _trial_id(r: TrialRecord) -> str:
    return r.label()  # the model's stable trial id — keys the raw artifacts too


def _slo_window_suffix(check: SloCheck) -> str:
    window = check.window_id or check.assertion.window.kind
    return "" if window == "measurement" else f" [window={window}]"


def _blocking_slo_skip(check: SloCheck) -> bool:
    return check.skipped and check.assertion.window.kind == "cooldown"


def _err_top(s: RequestStats) -> str:
    if not s.error_breakdown:
        return "-"
    kind, cnt = max(s.error_breakdown.items(), key=lambda kv: kv[1])
    return f"{kind}({cnt})"


def _stop_brief(r: TrialRecord) -> str:
    """One-line 'why it stopped early' from the TrialStop — reason + the trip SNAPSHOT
    (not the measurement aggregate) + the interrupted census. Used by md and html."""
    s = r.stop
    if s.snapshot:
        snap = s.snapshot
        detail = f"err {snap.error_rate * 100:.1f}% ({snap.errors}/{snap.sent}) @{snap.at_s:.0f}s"
    else:
        detail = "—"
    cut = f"，中断 {s.interrupted} 在途" if s.interrupted else ""
    return f"{_trial_id(r)} [{s.reason}]: {detail}{cut}"


def _phase_error_brief(r: TrialRecord) -> str:
    error = r.phase_errors[0]
    detail = f"{error.phase}: {error.error_type}"
    if error.message:
        detail += f": {error.message}"
    extra = len(r.phase_errors) - 1
    suffix = f"（另有 {extra} 个异常）" if extra else ""
    return f"{_trial_id(r)} [{detail}]{suffix}"


def _key_cells(r: TrialRecord) -> list[str]:
    return [r.arm.resources.label(), r.arm.load.model, f"{r.arm.load.schedule.peak_level:g}"]


def _stat_cells(s: RequestStats) -> list[str]:
    return [
        str(s.n),
        str(s.n_ok),
        f"{s.throughput_rps:.2f}",
        f"{s.error_rate * 100:.1f}",
        f"{s.drop_rate * 100:.1f}",
        f"{s.p50_ms:.0f}",
        f"{s.p95_ms:.0f}",
        f"{s.p99_ms:.0f}",
        _err_top(s),
    ]


def _probe_columns(results: list[TrialRecord], *, labeled: bool | None = None) -> list[str]:
    """Flat ``<series>.<stat>`` columns for measurement resource metrics,
    expanded from each trial's typed ``probe_metrics``. ``labeled`` filters by
    whether the series carries labels: ``False`` → only unlabeled (e.g. ``client.*``,
    for the §1 table); ``None`` → all (the CSV). Service-labeled metrics
    (``top.cpu_m{service=…}``) surface in the §3/§4 per-service charts instead of
    as flat columns."""
    cols: list[str] = []
    for r in results:
        for k in flatten(r.measurement.probe_metrics):
            if labeled is not None and ("{" in k) != labeled:
                continue
            if k not in cols:
                cols.append(k)
    return cols


def _probe_cells(r: TrialRecord, probe_cols: list[str]) -> list[str]:
    flat = flatten(r.measurement.probe_metrics)
    return [_fmt(flat.get(c)) for c in probe_cols]


def _window_probe_columns(results: list[TrialRecord]) -> list[str]:
    columns: list[str] = []
    for trial in results:
        for window in trial.windows:
            for column in flatten(window.probe_metrics):
                if column not in columns:
                    columns.append(column)
    return columns


def _metric_keys(results: list[TrialRecord]) -> list[str]:
    """Ordered union of per_request metric keys across trials (ttft_ms / first_…)."""
    keys: list[str] = []
    for r in results:
        for k in r.measurement.request.metrics:
            if k not in keys:
                keys.append(k)
    return keys


def _metric_columns(keys: list[str]) -> list[str]:
    """Expand per_request metric keys into ``<key>.p50`` / ``<key>.p95`` columns."""
    cols: list[str] = []
    for k in keys:
        cols += [f"{k}.p50", f"{k}.p95"]
    return cols


def _metric_cells(stats: RequestStats, keys: list[str]) -> list[str]:
    """p50/p95 cells for each per_request metric on a slice (``-`` when absent)."""
    cells: list[str] = []
    for k in keys:
        ms = stats.metrics.get(k)
        cells += [_fmt(ms.p50), _fmt(ms.p95)] if ms else ["-", "-"]
    return cells


def _metric_registry(results: list[TrialRecord]) -> dict[str, MetricFamily]:
    """Union of every trial's unified metric registry (families, all kinds), keyed by
    family name."""
    reg: dict[str, MetricFamily] = {}
    for r in results:
        reg.update(r.metrics)
    return reg


def _sorted_values(
    key: str, values: list[str], facet_order: dict[str, list[str]] | None
) -> list[str]:
    """Order facet values by the declared (ordinal) order, else alphabetically."""
    order = (facet_order or {}).get(key)
    if order:
        return sorted(values, key=lambda v: (order.index(v) if v in order else len(order), v))
    return sorted(values)


# ---------------------------------------------------------------------------
# Display layout (md/html ONLY — summary.csv stays flat for machine reading).
# The report table is narrow: one column per METRIC with its stats slashed
# ("p50/p95/p99"), and key columns that are constant across rows hoisted to a caption.
# ---------------------------------------------------------------------------

# request-side display columns: latency p50/p95/p99 merged into one `lat_ms` cell
_DISPLAY_STAT_COLS = ["n", "ok", "rps", "err%", "drop%", "lat_ms", "err_top"]
_DISPLAY_STAT_TIPS = {**_STAT_TIPS, "lat_ms": "request latency — p50/p95/p99 (ms)"}


def _display_stat_cells(s: RequestStats) -> list[str]:
    return [
        str(s.n),
        str(s.n_ok),
        f"{s.throughput_rps:.2f}",
        f"{s.error_rate * 100:.1f}",
        f"{s.drop_rate * 100:.1f}",
        f"{s.p50_ms:.0f}/{s.p95_ms:.0f}/{s.p99_ms:.0f}",
        _err_top(s),
    ]


def _display_metric_cells(stats: RequestStats, keys: list[str]) -> list[str]:
    """Per_request distribution → one ``p50/p95`` cell per metric (vs 2 flat columns)."""
    out: list[str] = []
    for k in keys:
        ms = stats.metrics.get(k)
        out.append(f"{ms.p50:.0f}/{ms.p95:.0f}" if ms else "-")
    return out


def _display_probe_cols(results: list[TrialRecord]) -> list[str]:
    """One column per UNLABELED probe series (labeled/service ones live in the
    §3/§4 per-service charts), not the
    flat ``<series>.<stat>`` explosion — e.g. one ``client.inflight`` col, not three.
    Synthesized ``.up`` health series stay out of the display table (healthy is
    silent; outages surface via §4/validity) — the CSV keeps them."""
    cols: list[str] = []
    for r in results:
        for sid in r.measurement.probe_metrics:
            if "{" in sid or sid in cols:  # unlabeled only, dedup
                continue
            if _series_family(sid).endswith(".up"):
                continue
            cols.append(sid)
    return cols


def _display_probe_cells(r: TrialRecord, cols: list[str]) -> list[str]:
    out: list[str] = []
    for sid in cols:
        summ = r.measurement.probe_metrics.get(sid)
        if summ is None:
            out.append("-")
            continue
        flat = flatten({sid: summ})  # {sid.stat: val}
        out.append("/".join(_fmt(flat[k]) for k in sorted(flat)))
    return out


def _key_layout(results: list[TrialRecord]) -> tuple[list[tuple[str, str]], list[str]]:
    """Hoist key columns CONSTANT across all rows into a caption; the rest stay table
    columns. A single-constraint sweep then shows just ``level`` (the swept axis)."""
    getters = {"constraint": lambda r: r.arm.resources.label(), "model": lambda r: r.arm.load.model}
    consts: list[tuple[str, str]] = []
    var_cols: list[str] = []
    for col, g in getters.items():
        vals = {g(r) for r in results}
        if len(vals) == 1:
            consts.append((col, next(iter(vals))))
        else:
            var_cols.append(col)
    var_cols.append("level")  # always a column — it's the sweep axis
    return consts, var_cols


def _key_cells_var(r: TrialRecord, var_cols: list[str]) -> list[str]:
    m = {
        "constraint": r.arm.resources.label(),
        "model": r.arm.load.model,
        "level": f"{r.arm.load.schedule.peak_level:g}",
    }
    return [m[c] for c in var_cols]


def _display_col_tips(
    results: list[TrialRecord], metric_keys: list[str], probe_cols: list[str]
) -> dict[str, str]:
    """Header tooltips for the merged display columns (which stats the cell stacks)."""
    reg = _metric_registry(results)
    tips = dict(_DISPLAY_STAT_TIPS)
    for k in metric_keys:
        d = reg.get(k)
        if d:
            tips[k] = f"{d.description} · p50/p95" if d.description else f"{k} · p50/p95"
    for sid in probe_cols:
        d = reg.get(_series_family(sid))
        if d:
            order = "/".join(sorted(LEGAL_STATS[d.value_kind]))
            tips[sid] = f"{d.description} · {order}" if d.description else f"{sid} · {order}"
    return tips


# chart naming: semantic name per unit so a chart says what it IS, not the bare unit
_CHART_UNIT_NAME = {
    "millicores": "CPU",
    "MiB": "内存",
    "count": "计数",
    "rps": "吞吐",
    "ms": "延迟",
    "s": "时长(s)",
}


def _unit_name(unit: str) -> str:
    return _CHART_UNIT_NAME.get(unit, unit or "metric")


# chart order within a service sub-section: CPU before 内存 before anything else
_UNIT_ORDER = {"millicores": 0, "MiB": 1}


def _unit_rank(unit: str) -> tuple[int, str]:
    return (_UNIT_ORDER.get(unit, 9), unit)


def _series_family(sid: str) -> str:
    """The family name of a bare series id: everything before the ``{labels}`` (a family
    name may itself contain dots — ``client.inflight`` — so DON'T rpartition on '.', which
    ``parse_ref`` does assuming a trailing ``.stat`` that a bare series id doesn't have)."""
    return sid.split("{", 1)[0]


def _series_value_kind(sid: str, registry: dict[str, MetricFamily]) -> str:
    fam = registry.get(_series_family(sid))
    return fam.value_kind if fam else "gauge"


def write_run(
    run: Run,
    runs_dir: str,
    *,
    facet_order: dict[str, list[str]] | None = None,
    knee_err_rate: float = 0.05,
    config_path: str | None = None,
) -> dict[str, str]:
    """Lay one ``Run`` down under ``<runs_dir>/<experiment>/<run_id>/`` — report.md +
    the csvs + a config snapshot + ``run.json`` — and append a one-line summary to
    the experiment's ``run.jsonl`` log.

    One config = one named *experiment* (eval_harness-style; its "arms" are the
    resources × load sweep). The ``Run`` is the first-class aggregate ``Engine.run()``
    returns; this just serialises it. Each run accumulates under the experiment dir
    rather than clobbering, so you keep history and can diff runs. Returns the
    artifact paths plus ``run_dir`` / ``run.json`` / ``run_log``.
    """
    run_dir = run_dir_for(runs_dir, run.experiment, run.run_id)
    exp_dir = run_dir.parent  # runs/<experiment>/ — holds the per-experiment run.jsonl log
    # Facts and the machine verdict must survive an optional renderer failure. The
    # report is a downstream view and therefore runs only after raw/model persistence.
    reduced = PerfReducer().reduce(run, run_dir)
    paths = {
        "run.json" if artifact.name == "model" else artifact.name: str(run_dir / artifact.path)
        for artifact in reduced
    }
    paths["run_dir"] = str(run_dir)

    # cross-harness verdict.json (run-level SLO gate) — what devloop reads to self-correct
    from perf_harness.verdict import write_verdict

    paths["verdict"] = str(write_verdict(run_dir, run))

    if config_path:
        try:
            source_config = Path(config_path).expanduser().resolve()
            raw_config = yaml.safe_load(source_config.read_text()) or {}
            caseset_ref = raw_config.get("caseset")
            if isinstance(caseset_ref, str) and caseset_ref:
                source_caseset = Path(caseset_ref).expanduser()
                if not source_caseset.is_absolute():
                    source_caseset = source_config.parent / source_caseset
                shutil.copyfile(source_caseset, run_dir / "caseset.yaml")
                raw_config["caseset"] = "./caseset.yaml"
                (run_dir / "config.yaml").write_text(yaml.safe_dump(raw_config, sort_keys=False))
                paths["caseset"] = str(run_dir / "caseset.yaml")
            else:
                shutil.copyfile(source_config, run_dir / "config.yaml")
            paths["config"] = str(run_dir / "config.yaml")
        except OSError:
            pass

    # append a one-line run summary to the experiment log (eval_harness run.jsonl style)
    try:
        slim = {
            "run_id": run.run_id,
            "created_at": run.created_at,
            "service": run.service,
            "n_trials": len(run.trials),
        }
        with (exp_dir / "run.jsonl").open("a") as f:
            f.write(json.dumps(slim, ensure_ascii=False) + "\n")
        paths["run_log"] = str(exp_dir / "run.jsonl")
    except OSError:
        pass

    # Views (report.md/html + summary/by_facet/windows csvs) are derived last.
    paths.update(
        write_report(
            run.trials,
            str(run_dir),
            knee_err_rate=knee_err_rate,
            facet_order=facet_order,
        )
    )

    return paths


def write_report(
    results: list[TrialRecord],
    outdir: str,
    *,
    knee_err_rate: float = 0.05,
    facet_order: dict[str, list[str]] | None = None,
) -> dict[str, str]:
    """Write summary.csv + by_facet.csv + timeseries.csv + report.md; return the paths."""
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    probe_cols = _probe_columns(results)  # CSV (flat, all series×stats); md/html merge per metric
    window_probe_cols = _window_probe_columns(results)
    metric_keys = _metric_keys(results)  # per_request metrics (ttft / first_…)
    mcols = _metric_columns(metric_keys)

    summary_path = out / "summary.csv"
    with summary_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(_KEY_COLS + _STAT_COLS + mcols + probe_cols)
        for r in results:
            w.writerow(
                _key_cells(r)
                + _stat_cells(r.measurement.request)
                + _metric_cells(r.measurement.request, metric_keys)
                + _probe_cells(r, probe_cols)
            )

    facet_path = out / "by_facet.csv"
    with facet_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["trial", "facet", "value", *_STAT_COLS, *mcols])
        for r in results:
            tid = _trial_id(r)
            for key in sorted(r.measurement.by_facet):
                for val in _sorted_values(key, list(r.measurement.by_facet[key]), facet_order):
                    sl = r.measurement.by_facet[key][val]
                    w.writerow([tid, key, val, *_stat_cells(sl), *_metric_cells(sl, metric_keys)])

    window_path = out / "windows.csv"
    with window_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "trial",
                "window_id",
                "name",
                "kind",
                "start_s",
                "end_s",
                "complete",
                "target_level",
                *_STAT_COLS,
                *mcols,
                *window_probe_cols,
            ]
        )
        for r in results:
            for window in r.windows:
                request_cells = (
                    _stat_cells(window.request)
                    if window.request is not None
                    else [""] * len(_STAT_COLS)
                )
                metric_cells = (
                    _metric_cells(window.request, metric_keys)
                    if window.request is not None
                    else [""] * len(mcols)
                )
                probe_values = flatten(window.probe_metrics)
                w.writerow(
                    [
                        r.label(),
                        window.id,
                        window.name,
                        window.kind,
                        window.start_s,
                        window.end_s,
                        window.complete,
                        window.target_level,
                        *request_cells,
                        *metric_cells,
                        *(_fmt(probe_values.get(column)) for column in window_probe_cols),
                    ]
                )

    # ``write_report`` remains useful standalone, so it materializes the same raw
    # series file as ``write_run_data``. In the full run path this is an idempotent
    # rewrite after the facts were already made durable.
    ts_path = write_timeseries_data(results, out)

    md_path = out / "report.md"
    md_path.write_text(_render_md(results, metric_keys, knee_err_rate, facet_order))

    # HTML companion: mirrors report.md, but §3 actually plots the Probe timeseries
    # (the md only points at timeseries.csv). Tables render offline; only the charts
    # pull ECharts from CDN.
    html_path = out / "report.html"
    doc = _build_doc(results, metric_keys, knee_err_rate, facet_order)
    html_path.write_text(render_html(doc), encoding="utf-8")

    return {
        "summary": str(summary_path),
        "by_facet": str(facet_path),
        "windows": str(window_path),
        "timeseries": str(ts_path),
        "report": str(md_path),
        "report_html": str(html_path),
    }


def _render_slo(lines: list[str], results: list[TrialRecord]) -> None:
    """Run gate: trial completeness, SLO checks, and confirmed capacity."""
    errors = [r for r in results if r.phase_errors]
    early = [r for r in results if r.stop.early and not r.phase_errors]
    if not any(r.slo for r in results) and not early and not errors:
        return
    has_fail = bool(early) or any(c.failed or _blocking_slo_skip(c) for r in results for c in r.slo)
    has_skip = any(c.skipped for r in results for c in r.slo)
    lines.append("## Run 判定（trial 完整性 + SLO）")
    lines.append("")
    verdict = (
        "ERROR ❌"
        if errors
        else ("FAIL ❌" if has_fail else ("PASS ✅（含 skipped，见下）" if has_skip else "PASS ✅"))
    )
    lines.append(f"**{verdict}**")
    lines.append("")
    if errors:
        lines.append("**执行异常**（不是请求失败或 SLO fail；本次 trial 未完整执行）：")
        for r in errors:
            lines.append(f"- `{_phase_error_brief(r)}`")
        lines.append("")
    if early:
        lines.append("**提前停止**（部分窗口不能确认该负载档容量）：")
        for r in early:
            lines.append(f"- `{_stop_brief(r)}`")
        lines.append("")
    for r in results:
        fails = [c for c in r.slo if c.failed]
        if not fails:
            continue
        lines.append(f"- `{_trial_id(r)}` ❌")
        for c in fails:
            a = c.assertion
            thr = f"{a.threshold[0]}..{a.threshold[1]}" if a.op == "between" else a.threshold
            obs = "—" if c.observed is None else f"{c.observed:.2f}"
            lines.append(f"  - {a.metric}{_slo_window_suffix(c)} = {obs}（需 {a.op} {thr}）")
    skipped = sorted(
        {c.assertion.metric + _slo_window_suffix(c) for r in results for c in r.slo if c.skipped}
    )
    if skipped:
        lines.append("")
        lines.append(
            "**skipped**（该 metric 的 slice 在此 trial 无数据 → 未判定，**别误读为通过**；"
            f"留意 label 拼写）：{', '.join(skipped)}"
        )
    if any(r.slo for r in results):
        cap = slo_aware_capacity(results)
        lines.append("")
        lines.append("**SLO-aware 容量**（完整 hold Window 内的全部匹配 SLO 均通过）：")
        for label, lvl in cap.items():
            lines.append(f"- `{label}`: {f'{lvl:g}' if lvl is not None else '—（无档达标）'}")
    lines.append("")


def _build_doc(
    results: list[TrialRecord],
    metric_keys: list[str],
    knee_err_rate: float,
    facet_order: dict[str, list[str]] | None,
) -> Report:
    """Map TrialRecords into the neutral report IR — same content/order as ``_render_md``,
    but §3 carries plotted Probe series instead of a pointer to timeseries.csv."""
    service = results[0].service if results else "?"
    report = Report(title=f"perf report — {service}", meta=[("service", service)])

    # Run gate: a partial trial fails before the three-state SLO rollup.
    errors = [r for r in results if r.phase_errors]
    early = [r for r in results if r.stop.early and not r.phase_errors]
    if any(r.slo for r in results) or early or errors:
        has_fail = bool(early) or any(
            c.failed or _blocking_slo_skip(c) for r in results for c in r.slo
        )
        has_skip = any(c.skipped for r in results for c in r.slo)
        verdict = (
            "ERROR ❌"
            if errors
            else ("FAIL ❌" if has_fail else ("PASS ✅（含 skipped）" if has_skip else "PASS ✅"))
        )
        sec = Section("Run 判定（trial 完整性 + SLO）", [Prose(verdict)])
        if errors:
            sec.blocks.append(
                Table(
                    ["execution error"],
                    [[_phase_error_brief(r)] for r in errors],
                )
            )
        if early:
            sec.blocks.append(
                Table(
                    ["early stop"],
                    [[_stop_brief(r)] for r in early],
                )
            )
        fail_rows: list[list[str]] = []
        for r in results:
            for c in (c for c in r.slo if c.failed):
                a = c.assertion
                thr = (
                    f"{a.threshold[0]}..{a.threshold[1]}" if a.op == "between" else str(a.threshold)
                )
                obs = "—" if c.observed is None else f"{c.observed:.2f}"
                metric = a.metric + _slo_window_suffix(c)
                fail_rows.append([_trial_id(r), metric, obs, a.op, thr])
        if fail_rows:
            sec.blocks.append(Table(["trial", "metric", "observed", "op", "threshold"], fail_rows))
        skipped = sorted(
            {
                c.assertion.metric + _slo_window_suffix(c)
                for r in results
                for c in r.slo
                if c.skipped
            }
        )
        if skipped:
            sec.blocks.append(
                Prose("skipped（slice 无数据 → 未判定，别误读为通过）：" + ", ".join(skipped))
            )
        if any(r.slo for r in results):
            cap = slo_aware_capacity(results)
            sec.blocks.append(
                KV(
                    [
                        (lbl, f"{lvl:g}" if lvl is not None else "—（无档达标）")
                        for lbl, lvl in cap.items()
                    ]
                )
            )
        report.sections.append(sec)

    # §1 overall summary — narrow: constant key cols hoisted to a caption, and each
    # metric is ONE slashed column (lat_ms = p50/p95/p99, client.inflight = last/mean/peak…).
    consts, var_cols = _key_layout(results)
    dprobe = _display_probe_cols(results)
    cols = var_cols + _DISPLAY_STAT_COLS + metric_keys + dprobe
    rows = [
        _key_cells_var(r, var_cols)
        + _display_stat_cells(r.measurement.request)
        + _display_metric_cells(r.measurement.request, metric_keys)
        + _display_probe_cells(r, dprobe)
        for r in results
    ]
    knees = _knees(results, knee_err_rate)
    knee_ids = {_trial_id(r) for r in knees.values()}
    highlight = {i: "knee" for i, r in enumerate(results) if _trial_id(r) in knee_ids}
    # hover a column header → the merged stats + what the metric is (unit · source).
    tips = _display_col_tips(results, metric_keys, dprobe)
    sec1 = Section("1. 汇总（overall）")
    if consts:
        sec1.blocks.append(Prose("固定：" + " · ".join(f"`{k}={v}`" for k, v in consts)))
    sec1.blocks.append(Table(cols, rows, highlight=highlight, col_tips=tips))
    if any(r.arm.load.model == "closed" for r in results):
        sec1.blocks.append(
            Prose(
                "闭环（closed）trial 的延迟百分位是 CO-biased"
                "（响应变慢时少采高延迟样本，尾延迟偏乐观）"
                "——勿当严格 SLO 尾延迟；要 CO-correct 的尾延迟用 open 模型。"
            )
        )
    if knees:
        knee_txt = "；".join(
            f"{c}: 约 {r.arm.load.label()} (err {r.measurement.request.error_rate * 100:.1f}%, "
            f"rps {r.measurement.request.throughput_rps:.1f})"
            for c, r in knees.items()
        )
        sec1.blocks.append(Prose(f"拐点（首个错误率越过阈值的档位）：{knee_txt}"))
    saturated = [r for r in results if r.measurement.request.drop_rate >= _SATURATION_FLAG]
    if saturated:
        sat_txt = "；".join(
            f"{_trial_id(r)}: drop {r.measurement.request.drop_rate * 100:.1f}% "
            f"({r.measurement.request.n_dropped} 未发出)"
            for r in saturated
        )
        sec1.blocks.append(
            Prose(
                f"⚠ 客户端饱和（drop ≥ {_SATURATION_FLAG * 100:.0f}%，该档延迟/吞吐不可信，"
                f"压力机蹭到 max_inflight）：{sat_txt}"
            )
        )
    errors = [r for r in results if r.phase_errors]
    if errors:
        error_txt = "；".join(_phase_error_brief(r) for r in errors)
        sec1.blocks.append(Prose(f"⚠ 执行异常（非请求失败或 SLO fail）：{error_txt}"))
    broke = [r for r in results if r.stop.early and not r.phase_errors]
    if broke:
        broke_txt = "；".join(_stop_brief(r) for r in broke)
        sec1.blocks.append(
            Prose(f"⚠ 提前停止（reason + 触发快照；数字为部分窗口，吞吐被低估）：{broke_txt}")
        )
    report.sections.append(sec1)

    # (no per-service resource TABLE: §3's response curves + §4's timeseries carry the
    # per-service story visually; the exact numbers live in summary.csv / run.json)

    # §2 per-dimension breakdown ("facet" = a request dimension like difficulty / lang)
    sec2 = Section("2. 按维度拆（如 difficulty/lang）")
    if not any(r.measurement.by_facet for r in results):
        sec2.blocks.append(Prose("（无分组维度；所有请求同质，无需拆）"))
    else:
        for r in results:
            if not r.measurement.by_facet:
                continue
            for key in sorted(r.measurement.by_facet):
                facet_rows = [
                    [
                        val,
                        *_display_stat_cells(r.measurement.by_facet[key][val]),
                        *_display_metric_cells(r.measurement.by_facet[key][val], metric_keys),
                    ]
                    for val in _sorted_values(key, list(r.measurement.by_facet[key]), facet_order)
                ]
                sec2.blocks.append(Prose(f"{_trial_id(r)} · {key}"))
                sec2.blocks.append(Table(["value", *_DISPLAY_STAT_COLS, *metric_keys], facet_rows))
    report.sections.append(sec2)

    # §2b observed Window breakdown
    windowed = [r for r in results if any(w.kind in ("ramp", "hold") for w in r.windows)]
    if windowed:
        sec2b = Section("2b. 按 Window 拆（schedule 的实际观测边界）")
        sec2b.blocks.append(
            Prose(
                "Stage 是负载计划，Window 是请求与资源指标共同使用的实际时间切片；"
                "容量只读取 complete 的 hold Window。"
            )
        )
        for r in windowed:
            window_rows = [
                [
                    window.id,
                    window.name,
                    window.kind,
                    "yes" if window.complete else "no",
                    *_display_stat_cells(window.request),
                    *_display_metric_cells(window.request, metric_keys),
                ]
                for window in r.windows
                if window.kind in ("ramp", "hold") and window.request is not None
            ]
            sec2b.blocks.append(Prose(_trial_id(r)))
            sec2b.blocks.append(
                Table(
                    ["window_id", "name", "kind", "complete", *_DISPLAY_STAT_COLS, *metric_keys],
                    window_rows,
                )
            )
        report.sections.append(sec2b)

    # §3 response curves — metric vs LOAD LEVEL (the response surface's x axis), one
    # sub-section per service. The "how does it change with pressure" question lives
    # here; needs ≥2 levels per constraint to be a curve at all (else omitted).
    sec3 = _response_section(results)
    if sec3 is not None:
        report.sections.append(sec3)

    # §4 timeseries — within-trial shape (ramp/plateau/spike), one sub-section per
    # service: left axis = that service's resources (usage + its OWN request/limit
    # reference lines), right axis = pressure (dashed; generator in-flight + the
    # service's own concurrency). Counters aren't plotted (they're in the CSV).
    report.sections.append(_timeseries_section(results))

    return report


def _curve_groups(results: list[TrialRecord]) -> list[tuple[str, list[TrialRecord]]]:
    """Resource-profile groups with ≥2 levels, each sorted by level — the sweeps that can
    be drawn as a curve (x = level)."""
    groups: dict[str, list[TrialRecord]] = {}
    for r in results:
        groups.setdefault(r.arm.resources.label(), []).append(r)
    return [
        (label, sorted(rs, key=lambda r: r.arm.load.schedule.peak_level))
        for label, rs in groups.items()
        if len({r.arm.load.schedule.peak_level for r in rs}) >= 2
    ]


def _response_section(results: list[TrialRecord]) -> Section | None:
    groups = _curve_groups(results)
    if not groups:
        return None
    sec = Section("3. 压力响应曲线（指标随档位）")
    sec.blocks.append(
        Prose(
            "x 轴=负载档位（closed 并发数 / open 到达率）。请求侧小节看入口的错误率与延迟"
            "随压力的变化；每个服务一小节，看它的资源用量逼近自己 request/limit 的速度"
            "（平线即参考线）。悬停图例可见各指标含义。"
        )
    )

    # 请求侧（入口）— error/drop + latency vs level; these are measured AT the entry
    sec.blocks.append(Heading("请求侧（入口）"))
    for clabel, rs in groups:
        pts = [(r.arm.load.schedule.peak_level, r) for r in rs]
        err = [(lv, round(r.measurement.request.error_rate * 100, 2)) for lv, r in pts]
        drop = [(lv, round(r.measurement.request.drop_rate * 100, 2)) for lv, r in pts]
        sec.blocks.append(
            Chart(
                f"错误率与丢弃 — {clabel}",
                [LineSeries("error %", err), LineSeries("drop %", drop)],
                x_label="load level",
                y_label="%",
            )
        )
        lat = [
            LineSeries(name, [(lv, getattr(r.measurement.request, attr)) for lv, r in pts])
            for name, attr in (("p50", "p50_ms"), ("p95", "p95_ms"), ("p99", "p99_ms"))
        ]
        ttft = [
            (lv, r.measurement.request.metrics["ttft_ms"].p95)
            for lv, r in pts
            if "ttft_ms" in r.measurement.request.metrics
        ]
        if ttft:
            lat.append(LineSeries("ttft p95", ttft))
        sec.blocks.append(Chart(f"延迟 — {clabel}", lat, x_label="load level", y_label="ms"))

    # 服务侧 — per service, per (unit, value_kind): the headline stat vs level.
    # gauge → peak (+ flat request/limit reference lines on bounded units);
    # counter → rate (window-independent, so levels with differing steady_s compare);
    # derived scalar → value ("did server-side ttft grow with pressure" is THIS curve).
    # The stat picks the y meaning, so kinds never share an axis.
    _CURVE_STAT = {"gauge": "peak", "counter": "rate", "scalar": "value"}
    _KIND_SUFFIX = {"gauge": "峰值", "counter": "速率", "scalar": "均值"}
    curves: dict[str, dict[tuple[str, str, str], dict[str, list[tuple[float, float]]]]] = {}
    for clabel, rs in groups:
        for r in rs:
            lv = r.arm.load.schedule.peak_level
            for family, fam in r.metrics.items():
                if fam.side != "resource":
                    continue
                stat = _CURVE_STAT.get(fam.value_kind)
                if stat is None:
                    continue
                if family.endswith(".up"):
                    continue  # health flatlines aren't curves; outages live in §4/validity
                # group strictly by the service label; EXTRA labels (a by:/per_pod
                # fan-out, e.g. {path=…} / {pod=…}) stay in the LINE identity — one
                # chart per (service, unit, kind) with one line per label value,
                # not one sub-section per label value.
                for sid, summary in r.measurement.probe_metrics.items():
                    name, labels, _ = parse_ref(sid)
                    if name != family or "service" not in labels:
                        continue
                    val = getattr(summary, stat, None)
                    if val is None:
                        continue
                    extra = {k: v for k, v in labels.items() if k != "service"}
                    line = series_id(family, extra)  # bare family when no extras
                    by_chart = curves.setdefault(labels["service"], {})
                    by_chart.setdefault((clabel, fam.unit, fam.value_kind), {}).setdefault(
                        line, []
                    ).append((lv, val))
    reg = _metric_registry(results)  # family → description (legend hover tooltip)
    for svc in sorted(curves):
        sec.blocks.append(Heading(svc))
        for (clabel, unit, vk), fams in sorted(
            curves[svc].items(), key=lambda kv: (kv[0][0], _unit_rank(kv[0][1]), kv[0][2])
        ):
            # usage families first, then the flat limits reference lines
            names = sorted(fams, key=lambda f: (f.startswith("limits."), f))
            stat = _CURVE_STAT[vk]
            # color/tip key off the bare family — a labeled line (family{path=…})
            # shares its family's pinned color/description
            lines = [
                LineSeries(
                    f"{f}.{stat}",
                    sorted(fams[f]),
                    color=_family_color(_series_family(f)),
                    tip=_family_tip(reg, _series_family(f)),
                )
                for f in names
            ]
            y = f"{unit}/s" if vk == "counter" else unit
            sec.blocks.append(
                Chart(
                    f"{svc} · {_unit_name(unit)}{_KIND_SUFFIX[vk]} — {clabel}",
                    lines,
                    x_label="load level",
                    y_label=y,
                )
            )
    return sec


def _family_tip(registry: dict[str, MetricFamily], family: str) -> str:
    """The family's human meaning — shown when hovering the chart legend (series
    names are addressing ids like ``metrics.sse_ok{…}``; this says what they ARE)."""
    fam = registry.get(family)
    return fam.description if fam else ""


def _tick_rate(series: Series) -> list[tuple[float, float]]:
    """A counter series → per-tick rate points (Δvalue ÷ Δt between samples) — the
    only way a cumulative line carries visual information. Negative deltas (counter
    reset, e.g. pod restart) clamp to 0; the summary carries the caveat."""
    pts: list[tuple[float, float]] = []
    samples = series.samples
    for a, b in zip(samples, samples[1:], strict=False):
        dt = b.t - a.t
        if dt > 0:
            pts.append((b.t, max(0.0, b.value - a.value) / dt))
    return pts


def _timeseries_section(results: list[TrialRecord]) -> Section:
    sec = Section("4. 时间序列（Probe 采样）")
    sec.blocks.append(
        Prose(
            "单档内的形状（爬坡/平台/尖峰）。左轴=该服务的资源（usage 配 request/limit "
            "参考平线），右轴=压力（虚线：发压机在途 + 该服务自身并发）。count 单位的 "
            "counter 画成逐 tick 速率（计数速率图）；累计原值见 `timeseries.csv`。"
            "悬停图例可见各指标含义。"
        )
    )
    # bucket each trial's series: (service, unit) → resource lines; count gauges →
    # the pressure pool (unlabeled = global generator-side, labeled = per service);
    # count-unit counters → per-tick rate lines (a service's own activity rhythm,
    # e.g. server-observed streams/errors per second as the press climbs)
    has = False
    svc_buckets: dict[str, list[tuple[TrialRecord, str, str, list[LineSeries]]]] = {}
    pressure: dict[tuple[int, str | None], list[LineSeries]] = {}
    for i, r in enumerate(results):
        buckets: dict[tuple[str, str, str], list[LineSeries]] = {}
        for sid, series in sorted(r.series.items()):
            if not series.samples:
                continue
            svc = split_series(sid)[1].get("service")
            if _series_family(sid).endswith(".up"):
                # observation health: chart ONLY when it dipped — the 0-valleys show
                # exactly when /metrics (or kubectl …) was down during the press
                if svc is None or min(s.value for s in series.samples) >= 1.0:
                    continue
                buckets.setdefault((svc, "up", "level"), []).append(
                    LineSeries(sid, [(s.t, s.value) for s in series.samples])
                )
                continue
            if _series_value_kind(sid, r.metrics) == "counter":
                # s-unit sums etc. carry no standalone visual meaning (their ratios
                # are the derive: scalars); only count-unit counters get rate lines
                if (series.unit or "") == "count":
                    rate_pts = _tick_rate(series)
                    if rate_pts:
                        fam = _series_family(sid)
                        line = LineSeries(
                            sid,
                            rate_pts,
                            color=_family_color(fam),
                            tip=_family_tip(r.metrics, fam),
                        )
                        if svc is None:
                            # client.sent is the actual open-loop send rate. Keep it
                            # alongside inflight as global pressure so every service
                            # chart can align load with its resource/count curves.
                            pressure.setdefault((i, None), []).append(line)
                        else:
                            buckets.setdefault((svc, "count", "rate"), []).append(line)
                continue
            pts = [(s.t, s.value) for s in series.samples]
            if (series.unit or "") == "count":
                pressure.setdefault((i, svc), []).append(
                    LineSeries(sid, pts, tip=_family_tip(r.metrics, _series_family(sid)))
                )
                continue
            if svc is None:
                continue  # resource series are service-bound today; nothing to anchor to
            buckets.setdefault((svc, series.unit or "", "level"), []).append(
                LineSeries(
                    sid,
                    pts,
                    color=_family_color(_series_family(sid)),
                    tip=_family_tip(r.metrics, _series_family(sid)),
                )
            )
        for (svc, unit, kind), lines in sorted(
            buckets.items(), key=lambda kv: (kv[0][0], _unit_rank(kv[0][1]), kv[0][2])
        ):
            svc_buckets.setdefault(svc, []).append((r, unit, kind, lines))
    for svc in sorted(svc_buckets):
        sec.blocks.append(Heading(svc))
        for r, unit, kind, lines in svc_buckets[svc]:
            i = results.index(r)
            right = pressure.get((i, None), []) + pressure.get((i, svc), [])
            has = True
            if kind == "rate":
                title = f"{svc} · 计数速率 — {_trial_id(r)}"
                y = f"{unit}/s"
            elif unit == "up":
                title = f"{svc} · 观测健康(up) — {_trial_id(r)}"
                y = "up (1=ok)"
            else:
                title = f"{svc} · {_unit_name(unit)} — {_trial_id(r)}"
                y = unit
            sec.blocks.append(
                Chart(
                    title,
                    lines,
                    y_label=y,
                    right_series=right,
                    y2_label="count / count·s⁻¹",
                )
            )
    if not has:
        sec.blocks.append(Prose("（无服务级 Probe 序列 — 资源观测见 observe: 配置）"))
    return sec


def _render_md(
    results: list[TrialRecord],
    metric_keys: list[str],
    knee_err_rate: float,
    facet_order: dict[str, list[str]] | None,
) -> str:
    lines: list[str] = []
    service = results[0].service if results else "?"
    lines.append(f"# perf report — {service}")
    lines.append("")

    _render_slo(lines, results)

    # §1 overall summary — narrow: constant key cols → caption, each metric one slashed col
    lines.append("## 1. 汇总（overall）")
    lines.append("")
    consts, var_cols = _key_layout(results)
    dprobe = _display_probe_cols(results)
    if consts:
        lines.append("固定：" + " · ".join(f"`{k}={v}`" for k, v in consts))
        lines.append("")
    header = var_cols + _DISPLAY_STAT_COLS + metric_keys + dprobe
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---"] * len(header)) + "|")
    for r in results:
        cells = (
            _key_cells_var(r, var_cols)
            + _display_stat_cells(r.measurement.request)
            + _display_metric_cells(r.measurement.request, metric_keys)
            + _display_probe_cells(r, dprobe)
        )
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    if any(r.arm.load.model == "closed" for r in results):
        lines.append(
            "> 闭环（closed）trial 的延迟百分位是 **CO-biased**（响应变慢时会少采高延迟样本，"
            "尾延迟偏乐观）——勿当严格 SLO 尾延迟；要 CO-correct 的尾延迟用 open 模型。"
        )
        lines.append("")

    knees = _knees(results, knee_err_rate)
    if knees:
        lines.append("**拐点**（首个错误率越过阈值的档位）：")
        for res_label, r in knees.items():
            lines.append(
                f"- `{res_label}`: 约在 {r.arm.load.label()} 处 "
                f"(err {r.measurement.request.error_rate * 100:.1f}%, "
                f"rps {r.measurement.request.throughput_rps:.1f})"
            )
        lines.append("")

    saturated = [r for r in results if r.measurement.request.drop_rate >= _SATURATION_FLAG]
    if saturated:
        lines.append(
            f"**⚠ 客户端饱和**（drop ≥ {_SATURATION_FLAG * 100:.0f}%，该档延迟/吞吐**不可信**——"
            "压力机蹭到 max_inflight，少发了请求，尾延迟被低估；调大 max_inflight 或降目标负载）："
        )
        for r in saturated:
            lines.append(
                f"- `{_trial_id(r)}`: drop {r.measurement.request.drop_rate * 100:.1f}% "
                f"({r.measurement.request.n_dropped} 个未发出)"
            )
        lines.append("")

    errors = [r for r in results if r.phase_errors]
    if errors:
        lines.append("**⚠ 执行异常**（不是请求失败或 SLO fail；本次 trial 未完整执行）：")
        for r in errors:
            lines.append(f"- `{_phase_error_brief(r)}`")
        lines.append("")

    broke = [r for r in results if r.stop.early and not r.phase_errors]
    if broke:
        lines.append(
            "**⚠ 提前停止**（trial 在计划窗口前停了——见 reason 与触发快照；数字为**部分窗口**，"
            "吞吐被低估，读作 '压到这档就崩了' 而非干净容量点）："
        )
        for r in broke:
            lines.append(f"- `{_stop_brief(r)}`")
        lines.append("")

    # (no per-service resource table — §3/§4 charts carry it; numbers in csv/run.json)

    # §2 per-dimension breakdown ("facet" = a request dimension like difficulty / lang)
    lines.append("## 2. 按维度拆（如 difficulty/lang）")
    lines.append("")
    if not any(r.measurement.by_facet for r in results):
        lines.append("- （无分组维度；所有请求同质，无需拆）")
        lines.append("")
    else:
        for r in results:
            if not r.measurement.by_facet:
                continue
            lines.append(f"### {_trial_id(r)}")
            lines.append("")
            for key in sorted(r.measurement.by_facet):
                lines.append(f"**{key}**")
                lines.append("")
                hdr = ["value", *_DISPLAY_STAT_COLS, *metric_keys]
                lines.append("| " + " | ".join(hdr) + " |")
                lines.append("|" + "|".join(["---"] * len(hdr)) + "|")
                for val in _sorted_values(key, list(r.measurement.by_facet[key]), facet_order):
                    sl = r.measurement.by_facet[key][val]
                    cells = [val, *_display_stat_cells(sl), *_display_metric_cells(sl, metric_keys)]
                    lines.append("| " + " | ".join(cells) + " |")
                lines.append("")

    # §2b observed Window breakdown
    windowed = [r for r in results if any(w.kind in ("ramp", "hold") for w in r.windows)]
    if windowed:
        lines.append("## 2b. 按 Window 拆（schedule 的实际观测边界）")
        lines.append("")
        lines.append(
            "> Stage 是负载计划，Window 是请求与资源指标共同使用的实际时间切片；"
            "容量只读取 complete 的 hold Window。"
        )
        lines.append("")
        for r in windowed:
            lines.append(f"### {_trial_id(r)}")
            lines.append("")
            hdr = ["window_id", "name", "kind", "complete", *_DISPLAY_STAT_COLS, *metric_keys]
            lines.append("| " + " | ".join(hdr) + " |")
            lines.append("|" + "|".join(["---"] * len(hdr)) + "|")
            for window in r.windows:
                if window.kind not in ("ramp", "hold") or window.request is None:
                    continue
                cells = [
                    window.id,
                    window.name,
                    window.kind,
                    "yes" if window.complete else "no",
                    *_display_stat_cells(window.request),
                    *_display_metric_cells(window.request, metric_keys),
                ]
                lines.append("| " + " | ".join(cells) + " |")
            lines.append("")

    # §3/§4 are charts (markdown can't carry them) — point at the html + the data layer
    lines.append("## 3. 压力响应曲线 / 4. 时间序列")
    lines.append("")
    lines.append(
        "图见 `report.html`：§3 按服务小节画指标随档位的响应曲线（入口小节含错误率/延迟），"
        "§4 按服务小节画单档内时间序列（左轴资源+request/limit 参考线，右轴压力虚线）。"
        "数据本体：`run.json`（全量模型）/ `timeseries.csv` / `outcomes.jsonl`；"
        "确定性观察：`python -m perf_harness.cli analyze <run_dir>`。"
    )
    lines.append("")
    return "\n".join(lines)


def _knees(results: list[TrialRecord], thr: float) -> dict[str, TrialRecord]:
    """First Trial per resource profile (by ascending level) whose overall error rate ≥ thr."""
    knees: dict[str, TrialRecord] = {}
    by_profile: dict[str, list[TrialRecord]] = {}
    for r in results:
        by_profile.setdefault(r.arm.resources.label(), []).append(r)
    for label, rs in by_profile.items():
        for r in sorted(rs, key=lambda x: x.arm.load.schedule.peak_level):
            if r.measurement.request.error_rate >= thr:
                knees[label] = r
                break
    return knees


def _fmt(v: float | None) -> str:
    if v is None:
        return "-"
    if v == int(v):
        return str(int(v))
    return f"{v:.2f}"
