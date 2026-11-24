import type { ScrapeGroup, MetricSampleCoverage } from "@compforge/harness-toolbox/metricdb";
import type { Run } from "../model";
import type { CallGraphMetric, CallGraphSnapshot } from "../callgraph";
import { rangeObservation } from "../observe/metric";
import { escape, section, table } from "./view";

export interface ServiceScrapeCoverage {
  service: string;
  groups: readonly ScrapeGroup[];
  issue?: string;
}

export interface MetricQualityBaseline {
  endTime: number;
  windowSeconds: number;
  inputs: {service:string; operation:string; metric:string; selectors:string[]; windowed:boolean}[];
  samples: Record<string, MetricSampleCoverage | {error:string}>;
}

function explanation(metric: CallGraphMetric): [string, string] {
  if (metric.value === "+Inf") return ["超过直方图最高有限桶 " + (metric.histogram?.max_finite_bucket ?? "?") + " " + metric.unit,
    "按业务耗时扩展 bucket 边界。"];
  if (metric.reason === "no_observations") return ["直方图观测增量为 0", "本窗口没有可计算分位数的事件。"];
  if (metric.status === "error") {
    if (metric.queries.some(q => q.error === "histogram bucket counts are not monotonic"))
      return ["直方图累计 bucket 增量不单调", "按原时间窗复查 bucket 增量，核对数据可见性及桶边界。"];
    return [metric.queries.some(q => q.warnings.length) ? "后端返回警告，结果可能不完整" : "查询或结果校验失败",
      "核对查询表达式和 run.json 中的响应。"];
  }
  if (metric.reason === "not_collected") return ["查询未完成", "核对数据源可用性和采集状态。"];
  return ["窗口无可计算值", "结合原始样本数、标签和分母增量核对原因。"];
}

/** Render the baseline separately from the observations for each realized window. */
export function metricQuality(run: Run, graph: CallGraphSnapshot | undefined, scrapes: readonly ServiceScrapeCoverage[],
  baseline?: MetricQualityBaseline): string {
  const services = new Set([run.service, ...scrapes.map(s => s.service), ...(baseline?.inputs.map(i => i.service) ?? []),
    ...(run.observation_bindings ?? []).map(b => b.service)]);
  const issues = new Map<string, unknown[][]>();
  const coverage = new Map<string, Set<string>>();
  const append = (service: string, row: unknown[]) => {
    services.add(service);
    const rows = issues.get(service) ?? [];
    rows.push(row);
    issues.set(service, rows);
  };
  for (const arm of graph?.arms ?? []) for (const window of arm.windows) {
    if (window.kind !== "full" && window.phase !== "hold") continue;
    for (const node of window.nodes) {
      const {service, metrics} = node.properties.perf;
      services.add(service);
      for (const [role, metric] of Object.entries(metrics)) {
        if (!metric.metric_ref || !metric.queries.length) continue;
        if (typeof metric.value === "number" && metric.status === "observed") {
          const observed = coverage.get(service) ?? new Set();
          observed.add(node.id + "." + role);
          coverage.set(service, observed);
        } else {
          append(service, [node.id, role, arm.arm_id + " / " + window.id, ...explanation(metric), metric.queries[0]?.expression]);
        }
      }
    }
  }
  for (const arm of run.executions) for (const query of arm.prometheus_observations ?? []) {
    if (query.step_s === null || !query.labels.operation || !query.labels.service) continue;
    const range = rangeObservation(arm, query);
    if (range.status !== "observed") append(query.labels.service, [query.labels.operation,
      query.metric.split(".").at(-1), arm.arm.id + " / 趋势", range.status === "query_error" ? "查询或结果校验失败" : range.status === "not_collected" ? "尚未采集" : "曲线无可用点",
      "核对滚动查询窗口、抓取周期和原始样本。", query.expression]);
  }
  const ordered = [...services].sort((a,b) => a === b ? 0 : a === run.service ? -1 : b === run.service ? 1 : a.localeCompare(b));
  return ordered.map(service => {
    const scrape = scrapes.find(item => item.service === service);
    const bindings = (run.observation_bindings ?? []).filter(b => b.service === service);
    const inputs = baseline?.inputs.filter(input => input.service === service) ?? [];
    const declared = new Set([...bindings.flatMap(b => Object.keys(b.metrics).map(m => b.target + "." + m)),
      ...inputs.map(i => i.operation + "." + i.metric)]);
    const rows = issues.get(service) ?? [];
    const counts = `观测指标 ${declared.size} 项 · 至少一个窗口有有限值 ${coverage.get(service)?.size ?? 0} 项 · 本轮问题记录 ${rows.length} 条`;
    let content = "<p>" + escape(counts) + "</p><h3>抓取情况</h3>";
    if (scrape?.issue) content += "<p>" + escape(scrape.issue) + "</p>";
    if (scrape?.groups.length) content += table(["Job","健康状态","Targets","抓取间隔（秒）","间隔来源","最早的最后抓取时间"],
      scrape.groups.map(g => [g.pool, g.health, g.count, g.intervalSeconds, g.intervalSource, g.lastScrape]));
    else if (!scrape?.issue) content += scrape ? "<p>未匹配到 active target。请核对 namespace、Service selector 和发现标签。</p>" : "<p>归档未记录此 Service 的抓取匹配结果。</p>";
    if (scrape?.groups.some(group => group.health === "down")) content += "<p>存在 down target。请检查采集器 Targets 页的 last error、网络连接和认证。</p>";
    if (scrape?.groups.some(group => group.intervalSeconds === undefined)) content += "<p>部分 job 的抓取间隔未知，请核对采集器的生效配置。</p>";
    const interval = Math.max(0, ...(scrape?.groups.map(g => g.intervalSeconds ?? 0) ?? []));
    if (interval) for (const arm of run.executions) {
      if (arm.arm.load.hold_s !== undefined) content += "<p>" + escape(`${arm.arm.id}：计划 hold ${arm.arm.load.hold_s} 秒，按最长抓取周期 ${interval} 秒估算约 ${(arm.arm.load.hold_s / interval).toFixed(1)} 个周期。`) + "</p>";
      const holds = arm.windows.filter(w => w.phase === "hold");
      for (const hold of holds) {
        const actual = hold.end_s - hold.start_s;
        if (actual < 4 * interval) content += "<p>" + escape(`${arm.arm.id} / ${hold.id}：实际 hold ${actual.toFixed(1)} 秒，最长抓取周期 ${interval} 秒。建议为相关 job 缩短间隔，或让实际 hold 达到 ${4*interval} 秒。`) + "</p>";
      }
    }
    content += "<h3>压测前的指标覆盖</h3>";
    if (!baseline) content += "<p>归档未记录压测前的样本密度。</p>";
    else if (!inputs.length) content += "<p>没有可核对的 Operation 指标查询计划。</p>";
    else {
      content += "<p>" + escape(`基线结束于 ${new Date(baseline.endTime*1000).toISOString()}，回看 ${baseline.windowSeconds} 秒。`) + "</p>";
      const samples = inputs.flatMap(i => i.selectors.length ? i.selectors.map(selector => {
        const s = baseline.samples[selector];
        const detail = !s ? "未核对" : "error" in s ? s.error : s.series === 0 ? "未匹配到时序" :
          `${s.series} 条时序，每条 ${s.minSamples}–${s.maxSamples} 个样本`;
        const advice = s && !("error" in s) ? !s.series ? "核对路由标签，观察产生流量后是否建立时序。" :
          i.windowed && (s.minSamples ?? 0) < 2 ? "部分时序不足两个样本，核对抓取间隔与窗口。" : "" : "";
        return [i.operation,i.metric,selector,detail,advice];
      }) : [[i.operation,i.metric,"","表达式无可直接核对的输入 selector","以本轮表达式结果为准。"]]);
      content += "<details><summary>查看各 Operation 的样本与建议</summary>" + table(["Operation","指标","输入 selector","基线样本","建议"],samples) + "</details>";
    }
    content += "<h3>本轮 Operation 指标</h3>";
    content += rows.length ? table(["Operation","指标","窗口","观测结果","建议","表达式"],rows)
      : "<p>" + (declared.size && coverage.get(service)?.size ? "已记录的查询未发现上述问题。" : "没有可评估的窗口指标。") + "</p>";
    return section(service,content);
  }).join("");
}
