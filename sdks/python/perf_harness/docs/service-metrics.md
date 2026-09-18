# 服务指标采集与报告

压测请求结果与服务指标是两种证据。请求错误率来自 perf 的 Judge；服务端耗时来自服务暴露的
指标。配置 `observe` 后，`MetricProbe` 默认直接抓服务的 `/metrics`，本地 Prombed 保存样本并执行
PromQL。未配置该 Probe 的实验不访问指标端点；已有远端 Prometheus 时可显式使用
`prometheus_query`，见 [扩展指南](extensions.md)。

## 配置与读取目标

以下指标名为示例，接入项目应替换为实际指标并补齐业务标签条件。首 token 指标必须真的对应
首个 token，不能把 start/heartbeat/首 chunk 的耗时当作 TTFT。

```yaml
service:
  name: api
  base_url: http://127.0.0.1:8080
runner: {name: http, method: POST, path: /chat}
load: {request_rate: 8, max_inflight: 80, hold_s: 60, cooldown_timeout_s: 600}
observe_interval_s: 5
observe:
  - name: api
    probes:
      - name: metric
        path: /metrics
        queries:
          - name: duration_rolling_s
            promql: sum(rate(request_duration_seconds_sum[1m])) / sum(rate(request_duration_seconds_count[1m]))
            unit: s
        summaries:
          - name: duration_mean_s
            promql: sum(increase(request_duration_seconds_sum[$window])) / sum(increase(request_duration_seconds_count[$window]))
            unit: s
          - name: ttft_mean_s
            promql: sum(increase(time_to_first_token_seconds_sum[$window])) / sum(increase(time_to_first_token_seconds_count[$window]))
            unit: s
report:
  columns:
    - {title: 错误率, metric: request.error_rate.value}
    - title: 服务端平均耗时（秒）
      metric: 'metric.duration_mean_s{service="api"}.value'
      window: {kind: observation}
    - title: 服务端首 token 平均耗时（秒）
      metric: 'metric.ttft_mean_s{service="api"}.value'
      window: {kind: observation}
```

`queries` 是趋势采样，`summaries` 是收尾时计算的窗口单值；二者名称不得重复。
每个 summary 的 `window` 默认 `{kind: observation}`，也可选择 measurement、hold 或 cooldown；
`$window` 仅替换为实际窗口长度，不解析或修改 PromQL 的业务选择条件。结果使用 `.value` 寻址。
均值应使用先按实例 `increase`、再 `sum` 的比值，不把实例均值或滚动均值再次平均。
PromQL 的 increase 含边界外推，服务指标是抓取样本上的估计，不等于逐请求原始记录。

- 单实例默认使用 `service.base_url + path`；`url` 可指定独立指标地址。
- 多个固定实例使用 `targets: [{url: ..., instance: ...}, ...]`；instance 是稳定的物理身份。
- Kubernetes 服务声明 `workloads` 时，默认经 toolbox 的既有 Workload 解析发现 Pod，并逐实例抓取。
  指标端口取 `service.metrics_port`，Pod UID 区分同 IP 的替代实例；标签 `instance` 保留在历史中。
  输出实例级序列时需在 query 的 `labels` 声明 instance；服务级结果用 PromQL 显式聚合。
- Workload 的 Kubernetes API 访问复用 Environment 客户端，包括已有 Host 后端；HTTP 抓取从 perf
  执行机发出，Pod 地址必须可达。配置完整 `url` 或 `targets` 时使用这些明确目标，不进行发现。
  不把负载均衡地址后交替出现的实例计数器拼成同一条时序。
- 隐式服务端点沿用该服务 headers；显式 URL/targets 的认证通过 Probe headers 单独配置，
  不继承压测请求凭据。采集使用独立连接池，容量由 `connection_pool_maxsize` 约束。

## 时间与完整性

setup 完成后先抓取基线，之后才开始发压计时；warmup/hold/cooldown 持续采集，收尾补最终抓取。
基线开销不占用计划的 hold 时间。窗口查询在释放本地样本前完成，Runner cleanup 仍负责业务资源。

`observation` 是从基线到最终抓取的观测窗口，覆盖 SSE 在 cooldown 中完成的请求；它不是新的负载阶段，
不改变 measurement/hold 的容量与请求归组语义。相对发压时钟的基线时间可以为负。
窗口查询保存实际 Unix 毫秒边界、展开后的表达式、来源 key、结果和失败原因，便于解释报告口径。

默认历史保留时间至少覆盖声明的负载时长、请求 cooldown 和额外观测时间；显式设置 `retention_ms`
则以该配置为准。`max_series`、`max_samples_per_series` 与字节/时间预算仍然生效；历史不足、目标
抓取失败、非有限结果等保留为缺失/错误，不产生伪造的零值或看似完整的窗口平均值。
没有请求时 `_count` 没有增量，平均耗时缺失是合法事实。Probe 故障不会默认改变业务判定；门禁仍须显式 SLO。

共享服务的指标可能包含其它流量；没有 run/case 标签时不能声称只归属本次压测。混合多个 case
的实验在汇总表中列出 case 集合，服务指标仍属于同一个服务时间窗口。

## 报告与离线重载

`report.columns` 选择汇总表中的指标列；请求速率、在途上限与 case 信息保留。每列通过统一
MetricStore 读取其 Window，HTML、Markdown 与 summary.csv 使用同样的列与结果。
选择器匹配多个同名阶段时，各窗口分别标注 ID，不隐式平均；没有值时显示 missing。

窗口结果和列配置保存在 run.json。`perf report` 从已有事实重渲染，不访问服务或 Prometheus，
不重新发压。趋势图继续使用周期样本，表格单值使用窗口结果，两者各自保留时间口径。
