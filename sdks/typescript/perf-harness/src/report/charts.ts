import { seriesId } from "../observe/metric";
import type { ArmRun, Run } from "../model";
import { peakLevel, saturated, stages } from "../load";
import { drop, escape, full, section } from "./view";
import { chartScript } from "./assets";

type Points = [number, number | null][];
type Lines = Map<string, Points>;
function chart(
  title: string,
  x: string,
  unit: string,
  lines: Lines,
  registry: Record<string, unknown>,
  right: Lines = new Map(),
  extent?: [number, number],
) {
  const tips: Record<string, string> = Object.create(null);
  const series = (source: Lines, secondary: boolean) =>
    Array.from(source, ([name, data]) => {
      const family = name.split("{")[0]!;
      const color = /^limits\.(cpu|mem)_limit$/.test(family)
        ? "#d62728"
        : /^limits\.(cpu|mem)_request$/.test(family)
          ? "#ff7f0e"
          : undefined;
      const meta = registry[family] as { description?: string } | undefined;
      if (meta?.description) tips[name] = meta.description;
      return {
        name,
        data,
        type: "line",
        smooth: false,
        showSymbol: true,
        itemStyle: { color },
        lineStyle: { color, type: secondary ? "dashed" : "solid" },
        yAxisIndex: secondary ? 1 : 0,
      };
    });
  const option = {
    title: { text: title, left: "center", textStyle: { fontSize: 13 } },
    tooltip: { trigger: "axis", renderMode: "richText" },
    legend: { top: 24, type: "scroll" },
    grid: { top: 64, left: 56, right: 56, bottom: 48 },
    xAxis: { type: "value", min: extent?.[0], max: extent?.[1], name: x, nameLocation: "middle", nameGap: 28 },
    yAxis: [
      { type: "value", name: unit },
      ...(right.size
        ? [
            {
              type: "value",
              name: "count / count·s⁻¹",
              splitLine: { show: false },
            },
          ]
        : []),
    ],
    dataZoom: [{ type: "inside" }, { type: "slider", height: 16, bottom: 8 }],
    series: [...series(lines, false), ...series(right, true)],
  };
  return `<div class="chart"></div><script>{const o=${JSON.stringify(option).replaceAll("<", "\\u003c")}, tips=${JSON.stringify(tips).replaceAll("<", "\\u003c")};o.legend.tooltip={show:true,renderMode:"richText",formatter:p=>tips[p.name]||p.name};echarts.init(document.currentScript.previousElementSibling).setOption(o);}</script>`;
}
function serviceOf(id: string) {
  const match = id.match(/[{,]service=("(?:[^"\\]|\\.)*")/);
  return match ? (JSON.parse(match[1]!) as string) : "";
}
function groups(arms: ArmRun[]) {
  const result = new Map<string, ArmRun[]>();
  for (const arm of arms) {
    const load = arm.arm.load,
      window = full(arm);
    if (
      !window ||
      (!saturated(load) && (window.limited_s ?? 0) > 0) ||
      (arm.phase_errors.length && !window.complete)
    )
      continue;
    const axis = saturated(load) ? "max_inflight" : "request_rate",
      scale = peakLevel(load) || 1;
    const conditions = {
      ...load,
      [axis]: (load[axis] ?? 0) / scale,
      stages: stages(load).map((s) => ({ ...s, [axis]: s[axis] / scale })),
    };
    const key = JSON.stringify(
      { resources: arm.arm.resources, load: conditions },
      (_, value) => {
        if (value && typeof value === "object" && !Array.isArray(value))
          return Object.fromEntries(
            Object.entries(value).sort(([a], [b]) => a.localeCompare(b)),
          );
        return value === Infinity ? "inf" : value;
      },
    );
    const group = result.get(key) ?? [];
    group.push(arm);
    result.set(key, group);
  }
  return [...result.values()]
    .filter((g) => new Set(g.map((a) => peakLevel(a.arm.load))).size >= 2)
    .map((g) =>
      g.sort((a, b) => peakLevel(a.arm.load) - peakLevel(b.arm.load)),
    );
}
function line(lines: Lines, name: string, x: number, y: unknown) {
  if (typeof y !== "number" || !Number.isFinite(y)) return;
  const pts = lines.get(name) ?? [];
  pts.push([x, y]);
  lines.set(name, pts);
}
export function charts(run: Run): string {
  const arms = run.executions;
  const responseGroups = new Map<string, string>(),
    timeGroups = new Map<string, string>();
  const append = (map: Map<string, string>, key: string, content: string) =>
    map.set(key, (map.get(key) ?? "") + content);
  const sections = (map: Map<string, string>) =>
    Array.from(
      map,
      ([name, html]) =>
        `<details class="sub" open><summary>${escape(name)}</summary>${html}</details>`,
    ).join("");
  for (const group of groups(arms)) {
    const axis = saturated(group[0]!.arm.load)
      ? "max_inflight (requests)"
      : "request_rate (requests/s)";
    const label = group.map((a) => a.arm.id).join(" / "),
      errors: Lines = new Map(),
      latency: Lines = new Map();
    const resources = new Map<string, Lines>();
    for (const a of group) {
      const w = full(a)!,
        s = w.request,
        level = peakLevel(a.arm.load);
      if (s) {
        if (s.n_judged) line(errors, "error %", level, s.error_rate * 100);
        line(errors, "drop %", level, drop(s) * 100);
        for (const p of ["p50", "p95", "p99"] as const)
          line(latency, p, level, s[`${p}_ms`]);
        line(
          latency,
          "ttft p95",
          level,
          s.metrics.ttft_ms?.p95 ?? s.metrics.first_token_ms?.p95,
        );
      }
      for (const [id, summary] of Object.entries(w.probe_metrics)) {
        const family = id.split("{")[0]!,
          svc = serviceOf(id);
        const meta = a.registry[family] as
          | { unit?: string; value_kind?: string; side?: string }
          | undefined;
        if (!svc || meta?.side !== "resource" || family.endsWith(".up"))
          continue;
        const stat =
          meta.value_kind === "counter"
            ? "rate"
            : meta.value_kind === "gauge"
              ? "peak"
              : "value";
        const key = JSON.stringify([svc, meta.unit, stat]),
          lines = resources.get(key) ?? new Map();
        line(
          lines,
          id,
          level,
          (summary as unknown as Record<string, unknown>)[stat],
        );
        if (lines.size) resources.set(key, lines);
      }
    }
    const registry = Object.assign({}, ...group.map((a) => a.registry));
    append(
      responseGroups,
      "请求侧（入口）",
      chart("错误率与丢弃 — " + label, axis, "%", errors, registry) +
        chart("延迟 — " + label, axis, "ms", latency, registry),
    );
    for (const [key, lines] of resources) {
      const [svc, unit, stat] = JSON.parse(key);
      append(
        responseGroups,
        svc,
        chart(
          `${svc} · ${unit} ${stat} — ${label}`,
          axis,
          stat === "rate" ? unit + "/s" : unit,
          lines,
          registry,
        ),
      );
    }
  }
  for (const a of arms) {
    const buckets = new Map<string, Lines>(),
      pressure = new Map<string, Lines>();
    for (const [id, points] of Object.entries(a.series ?? {})) {
      if (run.case_entries && id.startsWith("prometheus_query.") && id.includes("{operation=")) continue;
      const family = id.split("{")[0]!,
        svc = serviceOf(id);
      const meta = a.registry[family] as
        | { unit?: string; value_kind?: string }
        | undefined;
      let unit = meta?.unit ?? "",
        values = points;
      if (!points.length) continue;
      if (family.endsWith(".up")) {
        if (!svc || points.every(([, v]) => v >= 1)) continue;
        unit = "up";
      } else if (meta?.value_kind === "counter") {
        if (unit !== "count") continue;
        values = points.slice(1).flatMap(([t, v], i) => {
          const [prev, base] = points[i]!;
          return t > prev
            ? [[t, Math.max(0, v - base) / (t - prev)] as [number, number]]
            : [];
        });
        if (!values.length) continue;
        unit = "count/s";
      }
      const overlay = unit === "count" || (!svc && unit === "count/s");
      if (!overlay && !svc) continue;
      const map = overlay ? pressure : buckets,
        key = overlay ? svc : JSON.stringify([svc, unit]);
      const lines = map.get(key) ?? new Map();
      lines.set(id, values);
      map.set(key, lines);
    }
    for (const [key, lines] of buckets) {
      const [svc, unit] = JSON.parse(key);
      append(
        timeGroups,
        svc,
        chart(
          svc + " · " + unit + " — " + a.id,
          "t (s)",
          unit,
          lines,
          a.registry,
          new Map([...(pressure.get("") ?? []), ...(pressure.get(svc) ?? [])]),
        ),
      );
    }
  }
  const entries = run.case_entries ? entryCharts(run) : "";
  const response = sections(responseGroups),
    time = sections(timeGroups);
  return (
    (entries || response || time ? chartScript() : "") +
    (response
      ? section(
          "3. 压力响应曲线（指标随档位）",
          "<p>x 轴为 request_rate 或 max_inflight，另一轴与到达方式、阶段形态等条件固定。条件不同则分组；无法形成可比曲线时只保留各 Arm 的结果。请求侧小节看入口的错误率与延迟随压力的变化；每个服务一小节，看它的资源用量逼近自己 request/limit 的速度（平线即参考线）。悬停图例可见各指标含义。</p>" +
            response,
        )
      : "") +
    entries + (entries && !time ? "" : section(
      "4. 时间序列（Probe 采样）",
      "<p>单档内的形状（爬坡/平台/尖峰）。左轴=该服务的资源（usage 配 request/limit 参考平线），右轴=压力（虚线：发压机在途 + 该服务自身并发）。count 单位的 counter 画成逐 tick 速率（计数速率图）；累计原值见 `timeseries.csv`。悬停图例可见各指标含义。</p>" +
        (time || "<p>（无服务级 Probe 序列 — 资源观测见 observe: 配置）</p>"),
    ))
  );
}

function entryCharts(run: Run): string {
  const groups: string[] = [];
  for (const operation of run.entry_ids ?? []) {
    const cases = Object.entries(run.case_entries ?? {}).filter(([, entry]) => entry === operation).map(([id]) => id);
    const body = [`<p>Case：${cases.map(escape).join("、") || "自然流量"}</p>`];
    for (const arm of run.executions) {
      const window = full(arm);
      if (!window) continue;
      const extent: [number,number] = [window.start_s, window.end_s];
      const calls = new Map(arm.operation_runs.map(call => [call.id,call.outcome]));
      const duration: Lines = new Map(), first: Lines = new Map(), throughput: Lines = new Map();
      const bucket = Math.max(1, Math.ceil((extent[1]-extent[0])/120));
      for (const id of cases) {
        const counts = Array<number>(Math.ceil((extent[1]-extent[0])/bucket)).fill(0);
        for (const request of arm.requests) {
          if (request.case_id !== id || request.state !== "finished" || request.dispatched_at === undefined || request.finished_at === undefined) continue;
          const outcome = calls.get(request.operation_run_id ?? "");
          line(duration,id,request.dispatched_at,outcome?.duration_ms);
          line(first,id,request.dispatched_at,outcome?.metrics?.first_token_ms ?? outcome?.metrics?.ttft_ms);
          const index = Math.floor((request.finished_at-extent[0])/bucket);
          if (index >= 0 && index < counts.length) counts[index]!++;
        }
        counts.forEach((count,i) => {
          const start = extent[0]+i*bucket;
          line(throughput,id,start,count/Math.min(bucket,extent[1]-start));
        });
      }
      body.push(`<h4>${escape(arm.id)}</h4>`);
      for (const [title,unit,lines] of [["请求耗时","ms",duration],["首个输出耗时","ms",first],[`完成吞吐（每 ${bucket}s）`,"req/s",throughput]] as const)
        if (lines.size) body.push(chart(title,"t (s)",unit,lines,{},new Map(),extent));
      body.push("<p>服务端指标覆盖该 Operation 的全部匹配流量。</p>");
      const queries = arm.prometheus_observations?.filter(q => q.step_s !== null && q.labels.operation === operation) ?? [];
      if (!queries.length) body.push("<p>没有入口指标的时间序列。</p>");
      for (const q of queries) {
        const name = q.metric.replace(`prometheus_query.${operation}.`,"");
        const meta = arm.registry[q.metric] as {unit?:string;description?:string} | undefined;
        const points = arm.series?.[seriesId(q.metric,q.labels)] ?? [];
        if (!points.length || q.error) {
          body.push(`<p>${escape(name)} · ${q.error ? "查询失败：" + escape(q.error) : "窗口内无可用点"}</p>`);
          continue;
        }
        const data: Points = [];
        for (const [t,v] of points) {
          const previous = data.at(-1);
          if (previous && t-previous[0] > q.step_s! * 1.5) data.push([previous[0]+q.step_s!,null]);
          data.push([t,v]);
        }
        body.push(chart(name,"t (s)",meta?.unit ?? "",new Map([[name,data]]),{[name]:meta},new Map(),extent));
        body.push(`<p>${escape(meta?.description)} · 查询步长 ${q.step_s}s · ${points.length} 个有效点 · <code>${escape(q.expression)}</code></p>`);
      }
    }
    groups.push(section(operation,body.join("")));
  }
  return groups.length ? section("Case 与入口 Operation 时间趋势",groups.join("")) : "";
}
