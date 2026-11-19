# External metrics and offline Callgraph

The host owns Case/Runner selection, multiple-Case weights, target access and load authorization.
Perf owns load facts, metric query windows, projection and offline reports. Prometheus or
VictoriaMetrics already scrapes the target; this SDK never starts a scraper or stores a live TSDB.

## Host integration

1. Supply a Common Graph snapshot in `Experiment.graph`; its environment must match the service.
   Use `caseSet` / `caseMix` normally. The graph declares the selected workload's possible topology;
   mixed-Case metrics remain aggregate, not per-Case attribution.
2. Obtain a bounded `HttpApi` through the host's existing toolbox transport/client manager. Configure
   request concurrency, connection/request timeout and response byte limit there. Reuse that channel
   for the entire collection; the host releases it after collection, not after every query.
3. Use `operationMetricProbes` or `serviceMetricProbes` to bind Common observation declarations to the selected backend and their owning Operation, Service or Workload. The latter accepts a Service-scoped workload name. For standard Kubernetes resources, Toolbox supplies `workloadResourceObservation(service.workloads, namespace)`.
   Their expressions must scope environment/cluster/namespace/job as appropriate; target metadata
   does not silently add selectors. Backend-specific query semantics are not rewritten.
4. Run the Engine. Freeze the actual query plan, persist it and request facts, then collect metrics.
   Render only after collection; failure or cancellation retains the original load evidence.

```ts
import { MetricDBClient } from "@compforge/harness-toolbox/metricdb";
import {
  planMetrics, writeRunData, recollectMetrics, writePerfReport,
  type PrometheusProbe,
} from "@compforge/perf-harness";

// http: borrowed HttpApi with host-owned pooling, deadlines, TLS, cancellation and credentials.
const source = await MetricDBClient.connect(http, {
  url: "https://metrics.example/select/0/prometheus",
  headers: { Authorization: `Bearer ${token}` },
});
const probes: PrometheusProbe[] = [{
  service: "chat", target: "operation:chat/chat",
  method: "POST", path: "/chat", response_mode: "sse",
  summaries: [{
    name: "chat_qps", unit: "rps",
    promql: 'sum(rate(http_requests_total{cluster="customer-a",namespace="app",job="chat",method="POST",path="/chat"}[$window]))',
    description: "Completed HTTP requests, including other matching traffic",
    window: { kind: "full" },
  }],
  metrics: { qps: "chat_qps" },
  qps_semantics: "completed",
}];

// run is the Engine result; directory belongs to this invocation.
planMetrics(run, probes, source);
writeRunData(run, directory); // freezes queries before external I/O
const observed = await recollectMetrics(directory, [source], {
  concurrency: 4,
  signal,
  onProgress: event => logger.info(event),
});
writePerfReport(observed, directory);
```

`collectMetrics(run, sources)` also supports in-memory hosts. They own persistence and must save the
plan before calling it and the returned observations afterwards. `recollectMetrics` is the durable
path: archives both the previous and new collection, then atomically replaces only metric fields
in `run.json`; requests, evaluations and execution status remain unchanged. After cancellation it
persists partial query evidence and rethrows the cancellation. A report can still be rendered from
`loadRun(directory)`. Concurrent mutation of the same run directory is not supported; the host owns
invocation isolation.

`source.url` and `kind` must match saved queries before re-collection. Credentials remain in access
configuration, never in the source URL, Run or Callgraph. Refreshing credentials does not change the
query plan. Changing backend, target scope or query expressions requires a distinct observation plan;
it is not a retry of the existing plan.

## Observation contract and supported coverage

TypeScript currently collects **unlabeled instant Operation summaries**, at the end of full, phase
or custom windows. `$window` expands to the realized millisecond duration; query boundaries follow
`[start,end)` and exclude setup/cleanup. `started_at_s` is the actual load Unix origin, not the setup
start. The toolbox client also exposes `queryRange`, but range-series projection, Kubernetes probes
and Python's HTTP-profile query generator are not part of this TypeScript entrypoint. Unsupported
saved range plans fail before collection; they are never silently omitted.

`metrics` maps arbitrary display roles to named summaries. Default roles are `qps` (rps), `p95` (s)
and `error_rate` (ratio). Distinct producers may supply disjoint roles for one Operation; duplicate
roles or overlapping query addresses fail before changing the Run. Summary descriptions and units
must agree when a family is reused. Graph metadata, observation bindings, raw queries/results and
numeric summaries survive offline reload in the existing Run schema 12.

A scalar/vector query must produce one unlabeled value. Empty results remain missing; warnings,
undeclared labels, duplicate series, non-finite values and out-of-range error ratios are failures.
A genuine numeric zero remains observed. Retry removes old values for its query addresses so a failed
attempt cannot display yesterday's successful values. Other observations remain intact.

For classic histograms, use a `WindowQuery.histogram` such as `{quantile: 0.95, scale: 1e-6}` and
return bucket increments aggregated only by `le`. The reducer validates cumulative buckets and the
`+Inf` bucket, interpolates finite buckets and preserves overflow as `"+Inf"`. No samples and zero
population remain missing. Backend-specific increase functions belong to the project declaration.

## Reports and portability

`buildCallgraph(run)` derives schema 2 without I/O. `writePerfReport(run, directory)` writes
`callgraph.json` and `report.html`; `renderPerfReport(run)` returns HTML for hosts that own delivery.
Callgraph nodes show the Operation name and three RED metrics. They display `N/A` for a metric
without a finite value and `error` for a failed query. Open the full-window metrics table
for detailed statistics and reasons for unavailable values. The saved observations in `run.json` retain the exact PromQL, query
window, raw result and query diagnostics.
The report includes request windows and an interactive Operation graph with service/importance/QPS
filters, full/phase/custom selection, pan/zoom, tooltips and image copy. It makes no capacity or
bottleneck verdict. Actual edge execution still needs trace evidence.

The renderer embeds the repository's existing neutral Cytoscape/ELK graph assets and licenses at
build time, from `report-kit`. This is one browser-code source,
not a Python runtime dependency. Published ESM and generated HTML require no Python, source checkout,
CDN or runtime asset files. Both SDKs consume the same `operation-metrics` conformance fixture.

Operation declarations may contain complete queries: `operationMetricProbes(operations, source.kind)`
requires no host-provided label parameters. `$window` is bound only after execution, using actual windows.
The project owns all selectors. Declaring a Probe does not establish that the source stores its metrics:
empty results remain missing, errors remain errors, and successful sibling metrics remain available.
Neither backend detection nor metric collection scrapes exporters or discovers workloads.

## Query time and missing evidence

Toolbox evaluates a frozen query at its requested timestamp. For VictoriaMetrics it sends
`nocache=1` to disable instant evaluation shifts and range-grid alignment, together with
`latency_offset=1ms` to minimize range-tail substitution. Older VM versions reject zero offset.
This does not flush buffered samples or establish ingestion completeness. A later historical
recollection may recover late samples while retaining exactly the same expression and window.
Do not extend `$window` or move its end to obtain a nonempty result.

Callgraph distinguishes `unbound`, `not_collected`, `empty_result`, `no_observations` and
`query_error`. `empty_result` means only that the expression returned no result: missing source
series, too few samples and an expression filtering zero activity can all cause it. A validated
histogram with zero population supports `no_observations`; zero QPS alone does not establish the
denominator of another metric. Warnings and partial responses remain errors. The report explains
these states and preserves the query evidence rather than inferring scrape health.

Choose observation duration with the source's actual sample interval in mind. Counter increments
need multiple samples per series; a 60-second window over 60-second sampling may contain only one.
Longer collection waiting cannot add samples whose timestamps belong inside an already closed
window. Increase the planned observation duration for future runs, or keep the missing result.

For Case-oriented reports, save the selected Case-to-Operation mapping in
`run.case_entries` and its roots in `run.entry_ids`. An empty mapping with explicit roots observes natural traffic.
Each planned Operation metric with a full-window summary gets one range query per execution,
including downstream Operations. The rolling `$window` uses `trendWindowSeconds` (default 60).
The step is at least 15 seconds and scales to roughly 1,000 points for longer runs. Rolling
values can include observations before the run; instant window values still drive Callgraph.

Overview contains run status, summaries and coverage. Metrics shows request RED and custom
observations. The registered categories `red:qps`, `red:error_rate`, and `red:p95` bind standard
RED slots. Resources groups `resource:cpu` and `resource:mem` by Service. A `resource:workloads` series reports how many declared workloads matched at each sample. Resources shows its range alongside the totals. All other categories
retain their registered names and units under custom observations.

During load injection, every Metrics and Resources plot overlays the same execution-wide
`inflight` count on its right axis. Each dispatched request counts from dispatch until finish,
across all selected Cases. A request with no recorded finish counts through the end of the window.
Observation-only runs show collected metrics without an injected-load overlay.
