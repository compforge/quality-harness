import { escape, table, section, full, drop } from "./view";
import type { ArmRun, RequestStats, Run, Window } from "../model";
import { peakLevel, saturated, stages, observesOnly } from "../load";
import { selectedCells } from "./columns";

const statCols = [
  "dispatch_rps",
  "success_rps",
  "unjudged_rps",
  "interrupted",
  "completed_by_code",
  "throughput_by_code",
  "n",
  "n_pending",
  "n_interrupted",
  "ok",
  "completion_rps",
  "err%",
  "drop%",
  "lat_ms",
  "err_top",
];
function fixed(v: number | undefined, digits: number) {
  return v == null ? "—" : v.toFixed(digits);
}
function counts(value?: Record<string, number>) {
  return (
    "{" +
    Object.entries(value ?? {})
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([k, v]) => JSON.stringify(k) + ": " + v)
      .join(", ") +
    "}"
  );
}
function summaryFields(summary: object) {
  return Object.entries(summary)
    .filter(([k, v]) => (typeof v === "number" || v === "+Inf") && k !== "kind")
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, v]) => [
      k,
      typeof v === "number" && !Number.isInteger(v) ? v.toFixed(2) : String(v),
    ]);
}
function cells(s: RequestStats | undefined, keys: string[]): unknown[] {
  if (!s) return Array(statCols.length + keys.length).fill("N/A");
  const top = Object.entries(s.error_breakdown ?? {}).sort(
    (a, b) => b[1] - a[1],
  )[0];
  return [
    fixed(s.dispatch_rps, 2),
    fixed(s.success_rps, 2),
    fixed(s.unjudged_rps, 2),
    fixed(s.interrupted, 2),
    counts(s.completed_by_code),
    counts(s.throughput_by_code),
    s.n,
    s.n_pending,
    s.n_interrupted,
    s.n_ok,
    fixed(s.throughput_rps, 2),
    s.n_judged ? fixed(s.error_rate * 100, 1) : "—",
    fixed(drop(s) * 100, 1),
    [s.p50_ms, s.p95_ms, s.p99_ms].map((v) => fixed(v, 0)).join("/"),
    top ? `${top[0]}(${top[1]})` : "-",
    ...keys.map((k) => {
      const m = s.metrics?.[k];
      return m ? `${fixed(m.p50, 0)}/${fixed(m.p95, 0)}` : "-";
    }),
  ];
}
function constraint(a: ArmRun) {
  const r = a.arm.resources;
  return (
    [r.workers == null ? "" : "w" + r.workers, r.memory, r.cpu && "cpu" + r.cpu]
      .filter(Boolean)
      .join("/") || "default"
  );
}
function loadKey(a: ArmRun) {
  return [
    saturated(a.arm.load) ? "inf" : peakLevel(a.arm.load),
    observesOnly(a.arm.load)
      ? "N/A"
      : Math.max(...stages(a.arm.load).map((s) => s.max_inflight)),
  ];
}
export function apiRows(a: ArmRun, w?: Window) {
  return Object.entries(w?.probe_metrics ?? {})
    .filter(([sid]) => /[{,]operation=/.test(sid))
    .map(([sid, s]) => {
      const family = a.registry[sid.split("{")[0]!] as
        | { unit?: string; source?: string }
        | undefined;
      return [
        a.id,
        w!.id,
        sid,
        summaryFields(s)
          .map(([k, v]) => k + "=" + v)
          .join(", "),
        family?.unit,
        family?.source,
      ];
    });
}
export const apiHeader = [
  "arm_run",
  "window",
  "series",
  "statistics",
  "unit",
  "source",
];
export function reportSections(
  run: Run,
  facetOrder: Readonly<Record<string, readonly string[]>>,
  issues: readonly string[],
) {
  const arms = run.executions;
  const keys = [
    ...new Set(
      arms.flatMap((a) => Object.keys(full(a)?.request?.metrics ?? {})),
    ),
  ];
  const probeCols = [
    ...new Set(arms.flatMap((a) => Object.keys(full(a)?.probe_metrics ?? {}))),
  ].filter((k) => !k.includes("{") && !k.endsWith(".up"));
  const constraints = new Set(arms.map(constraint));
  const statHeaders = [...statCols, ...keys];
  const tips = Object.fromEntries(keys.map((k) => [k, "p50 / p95"]));
  tips.lat_ms = "request latency — p50/p95/p99 (ms)";
  const status = arms.map((a) => {
    const snap = a.stop.snapshot;
    const reason = a.phase_errors.length
      ? a.phase_errors
          .map((e) => `${e.phase}: ${e.error_type}: ${e.message}`)
          .join("；")
      : `${a.id} [${a.stop.reason}]: ${snap ? `err ${fixed(snap.error_rate * 100, 1)}% (${snap.errors}/${snap.completed}) @${fixed(snap.at_s, 0)}s` : "—"}${a.stop.interrupted ? "，中断 " + a.stop.interrupted + " 在途" : ""}`;
    return [
      a.id,
      a.phase_errors.length
        ? "error"
        : a.stop.interrupted || !["manual", "deadline"].includes(a.stop.reason)
          ? "stopped"
          : "completed",
      reason,
    ];
  });
  const warnings = arms.flatMap((a, i) => {
    const s = full(a)?.request;
    return [
      s && drop(s) >= 0.01
        ? `客户端饱和：${a.id} 丢弃 ${fixed(drop(s) * 100, 1)}%（${s.n_dropped} 未发出）`
        : "",
      status[i]![1] !== "completed" ? String(status[i]![2]) : "",
    ].filter(Boolean);
  });
  if (arms.some((a) => saturated(a.arm.load)))
    warnings.push(
      "闭环负载的延迟百分位存在 coordinated omission 偏差，尾延迟可能偏低。",
    );
  const varying = constraints.size > 1;
  const selected = run.report_columns ?? [];
  const summary = table(
    [
      ...(varying ? ["constraint"] : []),
      "peak_request_rate",
      "peak_max_inflight",
      ...(selected.length
        ? ["case", ...selected.map((c) => c.title)]
        : [...statHeaders, ...probeCols]),
    ],
    arms.map((a) => [
      ...(varying ? [constraint(a)] : []),
      ...loadKey(a),
      ...(selected.length
        ? [
            Object.keys(full(a)?.by_case ?? {})
              .sort()
              .join(", ") || "—",
            ...selectedCells(a, selected),
          ]
        : [
            ...cells(full(a)?.request, keys),
            ...probeCols.map(
              (k) =>
                summaryFields(full(a)?.probe_metrics[k] ?? {})
                  .map(([, v]) => v)
                  .join("/") || "-",
            ),
          ]),
    ]),
    tips,
  );
  const sections = [
    section("执行状态", table(["arm_run", "status", "reason"], status)),
    section(
      "1. 汇总（全程观测）",
      (constraints.size === 1
        ? `<p>固定：<code>constraint=${escape([...constraints][0])}</code></p>`
        : "") +
        "<p>加压速率是配置，服务吞吐来自实际观测，两者不等同。QPS=0 仅观测 hold，不执行 setup/warmup/cooldown/cleanup；请求指标不适用。正 QPS 默认范围覆盖 warmup、hold、cooldown，排除 setup/cleanup。吞吐按范围内完成事件计算；客户端延迟按范围内发起请求的完整耗时计算。n 为完成样本数，n_pending/n_interrupted 为该 cohort 未完成/中断数；服务端 p95 的完成样本口径可能不同。</p>" +
        summary +
        warnings.map((w) => `<p>⚠ ${escape(w)}</p>`).join(""),
    ),
  ];
  sections.push(
    section(
      "执行阶段与流量统计",
      "<p>setup → warmup → hold → cooldown → cleanup 串行、不重叠。setup/cleanup 只准备与清理，不计入流量观测或吞吐分母；时间以开始加压为零点。</p>" +
        table(
          ["arm", "phase", "start_s", "end_s", "duration_s", "complete"],
          arms.flatMap((a) =>
            a.phases.map((p) => [
              a.arm.id,
              p.kind,
              fixed(p.start_s, 3),
              fixed(p.end_s, 3),
              fixed(p.end_s - p.start_s, 3),
              p.complete ? "True" : "False",
            ]),
          ),
        ) +
        "<p>arrived 记录源头实际接受的发起机会；dispatch/completion/success 按实际事件时刻计数。延迟与错误率属于该窗口 dispatch 的请求，包含排空后完成的结果。cooldown 只等待已有请求退出；满在途时暂停源头，不产生丢弃或积压。丢弃没有 OperationRun，中断不伪造成功或失败响应。target_rps/cap 为 LoadPlan 最大配置值；阶段曲线见 run.json。</p>" +
        table(
          [
            "arm",
            "window",
            "target_rps",
            "cap",
            "arrived",
            "dispatched",
            "completed",
            "succeeded",
            "completed_by_code",
            "unjudged",
            "dropped",
            "interrupted",
            "dispatch_rps",
            "completion_rps",
            "inflight_peak",
            "inflight_end",
            "limited_s",
            "end_reason",
          ],
          arms.flatMap((a) =>
            a.windows.flatMap((w) => {
              const s = w.request;
              return w.kind === "phase" && s
                ? [
                    [
                      a.arm.id,
                      w.phase,
                      ...loadKey(a),
                      s.arrived,
                      s.dispatched,
                      s.completed,
                      s.succeeded,
                      counts(s.completed_by_code),
                      s.unjudged,
                      s.n_dropped,
                      s.interrupted,
                      fixed(s.dispatch_rps, 2),
                      fixed(s.throughput_rps, 2),
                      s.inflight_peak,
                      s.inflight_end,
                      fixed(w.limited_s, 3),
                      w.end_reason,
                    ],
                  ]
                : [];
            }),
          ),
        ),
    ),
  );
  const api = arms.flatMap((a) => apiRows(a, full(a)));
  if (!run.graph && api.length)
    sections.push(section("API 观测（全程）", table(apiHeader, api)));
  const facets = arms.flatMap((a) =>
    Object.entries(full(a)?.by_facet ?? {})
      .sort(([x], [y]) => x.localeCompare(y))
      .map(([key, values]) => {
        const order = facetOrder[key] ?? [];
        const rank = (v: string) =>
          order.includes(v) ? order.indexOf(v) : order.length;
        return (
          `<p>${escape(a.id)} · ${escape(key)}</p>` +
          table(
            ["value", ...statHeaders],
            Object.keys(values)
              .sort((x, y) => rank(x) - rank(y) || x.localeCompare(y))
              .map((v) => [v, ...cells(values[v], keys)]),
            tips,
          )
        );
      }),
  );
  sections.push(
    section(
      "2. 按维度拆（如 difficulty/lang）",
      facets.join("") || "<p>（无分组维度；所有请求同质，无需拆）</p>",
    ),
  );
  const windowed = arms.filter((a) => a.windows.some((w) => w.kind !== "full"));
  if (windowed.length)
    sections.push(
      section(
        "2b. 统计范围（阶段 / 自定义）",
        "<p>执行阶段与统计范围独立；以下按实际起止选择证据，跨范围完成的请求仍保留完整耗时。</p>" +
          windowed
            .map(
              (a) =>
                `<p>${escape(a.id)}</p>` +
                table(
                  [
                    "window_id",
                    "name",
                    "kind",
                    "phase",
                    "start_s",
                    "end_s",
                    "complete",
                    ...statHeaders,
                  ],
                  a.windows
                    .filter((w) => w.kind !== "full" && w.request)
                    .map((w) => [
                      w.id,
                      w.name,
                      w.kind,
                      w.phase,
                      fixed(w.start_s, 3),
                      fixed(w.end_s, 3),
                      w.complete ? "yes" : "no",
                      ...cells(w.request, keys),
                    ]),
                  tips,
                ),
            )
            .join(""),
      ),
    );
  if (issues.length)
    sections.push(
      `<details><summary>指标覆盖明细（${issues.length}）</summary><ul>${issues.map((i) => `<li>${escape(i)}</li>`).join("")}</ul></details>`,
    );
  return sections.join("");
}
