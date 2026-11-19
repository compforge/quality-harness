import { workloadObservationTarget } from "../observe/operations";
import { serviceId } from "@compforge/harness-common";
import { rangeObservation } from "../observe/metric";
import type { ArmRun, Run } from "../model";
import { escape, full, section } from "./view";
import { observesOnly } from "../load";

type Points = [number, number | null][];
type Extent = [number, number];
type Series = { name: string; data: Points };
type Group = "请求 RED" | "资源耗费" | "自定义指标";
const resourceTitles = new Map([
  ["resource:cpu", "CPU"], ["resource:mem", "内存"], ["resource:workloads", "Workload 覆盖"],
]);

function groupOf(category?: string): Group {
  if (resourceTitles.has(category ?? "")) return "资源耗费";
  switch (category) {
    case "red:qps":
    case "red:error_rate":
    case "red:p95":
      return "请求 RED";
    default:
      return "自定义指标";
  }
}

function chart(title: string, unit: string, lines: Series[], extent: Extent, overlay?: Points) {
  const series = lines.map((line) => ({ ...line, type: "line", showSymbol: true }));
  const axes: object[] = [{ type: "value", name: unit }];
  const pressure = {
    name: "inflight",
    data: overlay,
    type: "line",
    step: "end",
    showSymbol: false,
    itemStyle: { color: "#475569" },
    lineStyle: { type: "dashed" },
  };
  if (overlay) axes.push({ type: "value", name: "inflight", minInterval: 1, min: 0, splitLine: { show: false } });
  const option = {
    title: { text: title, left: "center", textStyle: { fontSize: 13 } },
    tooltip: { trigger: "axis", renderMode: "richText" },
    legend: { top: 24, type: "scroll" },
    grid: { top: 64, left: 56, right: 56, bottom: 48 },
    xAxis: {
      type: "value",
      min: extent[0],
      max: extent[1],
      name: "t (s)",
      nameLocation: "middle",
      nameGap: 28,
    },
    yAxis: axes,
    dataZoom: [{ type: "inside" }, { type: "slider", height: 16, bottom: 8 }],
    series: overlay ? [...series, { ...pressure, yAxisIndex: 1 }] : series,
  };
  return `<div class="chart"></div><script>echarts.init(document.currentScript.previousElementSibling).setOption(${JSON.stringify(option).replaceAll("<", "\\u003c")});</script>`;
}

/** @spec Count dispatched requests across all Cases on [dispatch, finish); a missing finish stays open through the window. */
function inflightSeries(arm: ArmRun, [start, end]: Extent): Points {
  const changes = new Map<number, number>([[start, 0], [end, 0]]);
  for (const { dispatched_at: dispatch, finished_at: finish } of arm.requests) {
    if (dispatch === undefined || dispatch > end || (finish !== undefined && finish <= start)) continue;
    const entered = Math.max(start, dispatch);
    changes.set(entered, (changes.get(entered) ?? 0) + 1);
    if (finish !== undefined && finish <= end) changes.set(finish, (changes.get(finish) ?? 0) - 1);
  }
  const points: Points = [];
  let count = 0;
  for (const [time, delta] of [...changes].sort(([a], [b]) => a - b)) {
    count += delta;
    if (delta || time === start || time === end) points.push([time, count]);
  }
  return points;
}

function requestTrends(arm: ArmRun, extent: Extent, inflight: Points): string {
  const [start, end] = extent;
  const width = Math.max(1, Math.ceil((end - start) / 120));
  const buckets = Array.from({ length: Math.ceil((end - start) / width) }, () => ({
    sent: 0,
    completed: 0,
    judged: 0,
    errors: 0,
  }));
  const bucketAt = (time: number) =>
    time >= start && time < end ? buckets[Math.floor((time - start) / width)] : undefined;
  const calls = new Map(arm.operation_runs.map((call) => [call.id, call.outcome]));
  const durations = new Map<string, Points>();
  for (const request of arm.requests) {
    if (request.state === "finished" && request.finished_at !== undefined) {
      const bucket = bucketAt(request.finished_at);
      if (bucket) bucket.completed++;
    }
    if (request.dispatched_at === undefined) continue;
    const bucket = bucketAt(request.dispatched_at);
    if (!bucket) continue;
    bucket.sent++;
    if (request.state !== "finished" || !request.operation_run_id) continue;
    const evaluation = arm.evaluations[request.operation_run_id];
    if (evaluation) {
      bucket.judged++;
      if (evaluation.code !== "success") bucket.errors++;
    }
    const duration = calls.get(request.operation_run_id)?.duration_ms;
    if (duration !== undefined && Number.isFinite(duration)) {
      const points = durations.get(request.case_id) ?? [];
      points.push([request.dispatched_at, duration]);
      durations.set(request.case_id, points);
    }
  }
  const rate = (key: "sent" | "completed"): Points =>
    buckets.map((bucket, i) => {
      const t = start + i * width;
      return [t, bucket[key] / Math.min(width, end - t)];
    });
  const qps = rate("sent");
  let html = chart("实际发送 QPS", "req/s", [{ name: "request_qps", data: qps }], extent, inflight);
  if (arm.requests.length) {
    html += chart("完成吞吐", "req/s", [{ name: "completion_qps", data: rate("completed") }], extent, inflight);
    if (buckets.some((b) => b.judged)) {
      const data: Points = buckets.map((b, i) => [
        start + i * width,
        b.judged ? (100 * b.errors) / b.judged : null,
      ]);
      html += chart("请求错误率", "%", [{ name: "error_rate", data }], extent, inflight);
    }
    if (durations.size)
      html += chart(
        "请求耗时",
        "ms",
        Array.from(durations, ([name, data]) => ({ name, data })),
        extent,
        inflight,
      );
  }
  return html;
}

/** @spec Replay registered series by owner; only actual dispatches contribute to the common load overlay. */
export function charts(run: Run) {
  const groups: Record<Exclude<Group, "资源耗费">, string[]> = { "请求 RED": [], 自定义指标: [] };
  const resources = new Map<string, { parts: string[]; hasSamples: boolean }>();
  for (const arm of run.executions) {
    const window = full(arm);
    if (!window || window.end_s <= window.start_s) continue;
    const extent: Extent = [window.start_s, window.end_s];
    const inflight = observesOnly(arm.arm.load) ? undefined : inflightSeries(arm, extent);
    if (inflight) groups["请求 RED"].push(section(arm.id, requestTrends(arm, extent, inflight)));
    const plots = new Map<
      string,
      { group: Group; owner: string; title: string; unit: string; lines: Series[]; notes: string[] }
    >();
    function add(
      id: string,
      service: string,
      target: string | undefined,
      name: string,
      points: Points,
      note?: string,
    ) {
      const meta = arm.registry[id.split("{")[0]!] as
        | { category?: string; unit?: string; description?: string }
        | undefined;
      const group = groupOf(meta?.category),
        unit = meta?.unit ?? "";
      const title = resourceTitles.get(meta?.category ?? "") ?? name;
      const owner = group === "资源耗费" ? service : target ?? service;
      if (group === "资源耗费" && target) name = target + " · " + name;
      const key = JSON.stringify([owner, title, unit, group]);
      const plot = plots.get(key) ?? { group, owner, title, unit, lines: [], notes: [] };
      if (meta?.category === "resource:workloads") {
        name = title;
        const counts = points.flatMap(([, value]) => value === null ? [] : [value]);
        if (counts.length) {
          const low = Math.min(...counts), high = Math.max(...counts);
          note = `每个采样时刻定位到 ${low === high ? low : low + "–" + high} 个 Workload`;
        }
      } else if (points.length) {
        plot.lines.push({ name, data: points });
      }
      if (meta?.description || note)
        plot.notes.push(
          `<p>${escape(name)} · ${escape([meta?.description, note].filter(Boolean).join(" · "))}</p>`,
        );
      plots.set(key, plot);
    }
    const queried = new Set<string>();
    for (const query of arm.prometheus_observations ?? []) {
      if (query.step_s === null) continue;
      const { id, status, points: samples } = rangeObservation(arm, query);
      queried.add(id);
      const workload = query.labels.workload;
      const service = query.labels.service ?? run.service;
      const owner = workload ? workloadObservationTarget(service, workload) : query.labels.operation;
      const family = query.metric.replace(/^prometheus_query\./, "");
      const prefix = (owner ?? serviceId(service)) + ".";
      const name = family.startsWith(prefix) ? family.slice(prefix.length) : family;
      const points: Points = [];
      for (const [t, v] of samples) {
        const prev = points.at(-1);
        if (prev && t - prev[0] > query.step_s * 1.5) points.push([prev[0] + query.step_s, null]);
        points.push([t, v]);
      }
      const note = status === "query_error"
        ? `查询失败：${query.error}`
        : status === "not_collected"
          ? "尚未采集"
          : points.length
            ? `${samples.length} 个有效点`
            : "窗口内无可用点";
      add(id, service, owner, name, points, note);
    }
    for (const [id, points] of Object.entries(arm.series ?? {})) {
      if (queried.has(id) || !points.length) continue;
      const service = id.match(/[{,]service=("(?:[^"\\]|\\.)*")/);
      add(id, service ? JSON.parse(service[1]!) : run.service, undefined, id, points);
    }
    const owners = new Map<string, { group: Group; owner: string; html: string[]; hasSamples: boolean }>();
    for (const plot of plots.values()) {
      const key = JSON.stringify([plot.group, plot.owner]);
      const block = owners.get(key) ?? { group: plot.group, owner: plot.owner, html: [], hasSamples: false };
      if (plot.lines.length) block.hasSamples = true;
      if (plot.lines.length) block.html.push(chart(plot.title, plot.unit, plot.lines, extent, inflight));
      block.html.push(...plot.notes);
      owners.set(key, block);
    }
    for (const { group, owner, html, hasSamples } of owners.values()) {
      if (group === "资源耗费") {
        const service = resources.get(owner) ?? { parts: [], hasSamples: false };
        service.parts.push(section(arm.id, html.join("")));
        service.hasSamples ||= hasSamples;
        resources.set(owner, service);
        continue;
      }
      const cases = Object.entries(run.case_entries ?? {})
        .filter(([, entry]) => entry === owner)
        .map(([id]) => id);
      groups[group].push(
        section(
          `${owner} · ${arm.id}`,
          (cases.length ? `<p>Case：${cases.map(escape).join("、")}</p>` : "") + html.join(""),
        ),
      );
    }
  }
  const panels = Object.entries(groups)
    .filter(([, parts]) => parts.length)
    .map(([name, parts]) => section(name, parts.join("")));
  const serviceSections = [...resources]
    .sort(([a, left], [b, right]) => Number(right.hasSamples) - Number(left.hasSamples) || a.localeCompare(b))
    .map(([name, service]) => section(service.hasSamples ? name : name + " · 无样本", service.parts.join("")));
  return { trends: panels.join(""), resources: serviceSections.join("") };
}
