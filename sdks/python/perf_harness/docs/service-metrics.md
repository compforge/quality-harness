# 服务指标查询与报告

项目可复用的 Graph / Observation / Experiment 配置优先见 [观测声明](observation.md)。
本文说明底层查询、补采和已有内联 `observe` 接入。

Perf 记录请求结果，并在压测结束后查询外部 Prometheus 的服务指标。请求错误率来自独立 Judge；
服务端指标用于解释同一时间窗口内的行为，二者分别保留。

Perf 不自行抓取服务 `/metrics`：响应可能较大，周期性全量抓取会增加传输和解析开销，影响
观测时效与完整性。复用已有 Prometheus 的持续采集和历史存储，让 Perf 只读取所需查询结果。
Toolbox 的通用 MetricDataSource 仍供其它采集场景使用，不由 Perf 自动启用或作为失败回退。

## 实验 YAML

Prometheus 地址配置在实验顶层，所有内置和项目注册的指标 Probe 共用。地址是服务器 base URL，
可包含反向代理前缀。鉴权只引用环境变量名，不继承压测请求头，也不将凭据写入 Run。

```yaml
service: {name: api, base_url: 'http://api:8080'}
runner: {name: http, method: POST, path: /generate}
load: {request_rate: 8, max_inflight: 100, hold_s: 60, cooldown_timeout_s: 600}
prometheus:
  url: https://prometheus.example/prometheus
  # 可选；变量的值是完整请求头值，例如 Bearer <token>。
  headers_env:
    Authorization: PERF_PROMETHEUS_AUTHORIZATION
  timeout_ms: 10000
  step_s: 15
observe:
  - name: api
    probes:
      - name: prometheus_query
        operation: generate
        queries:
          - name: requests_rps
            promql: sum by (status_code) (rate(api_requests_total{job="api"}[1m]))
            labels: [status_code]
            unit: rps
        summaries:
          - name: duration_mean_s
            promql: sum(increase(api_duration_seconds_sum{job="api"}[$window])) / (sum(increase(api_duration_seconds_count{job="api"}[$window])) > 0)
            unit: s
report:
  columns:
    - {title: 请求错误率, metric: request.error_rate.value}
    - title: 服务端平均耗时（秒）
      metric: 'prometheus_query.duration_mean_s{service="api",operation="operation:api/generate"}.value'
      window: {kind: full}
```

不需要认证时省略 `headers_env`。`timeout_ms` 默认 5000，限制单次查询的连接等待、响应头与
响应体读取总时长；`max_response_bytes` 默认 16 MiB，`max_series` 默认 20000，
`connection_pool_maxsize` 默认 8。超限报错，不截断结果。`step_s` 默认 15 秒，是历史查询的
求值步长，不改变 Prometheus 的抓取间隔；设置更小的 step 不会产生更高分辨率的原始样本。

`queries` 通过 `/api/v1/query_range` 获取趋势，`summaries` 通过 `/api/v1/query` 获取窗口单值。
summary 的 `window` 默认 `{kind: full}`；指定阶段用 `{kind: phase, phase: hold}`，
也可选择 warmup/cooldown 或 `{kind: custom, name: steady}`；
`$window` 替换为实际窗口长度，结果使用 `.value` 寻址。滚动均值的采样平均不等于整窗请求加权均值；
整窗均值应使用各实例 sum/count 的 increase 聚合比值。指标、路由和环境过滤条件由项目声明。

旧 `name: metric` 配置已移除并在加载时明确报错。删除 path、targets、metric_names、retention_ms、
transport 等自抓配置，改用顶层 prometheus 和 `name: prometheus_query`；指标引用前缀也改为
`prometheus_query`。Python 接入使用 `PrometheusQueryProbe(data_source=..., queries=..., summaries=...)`。
自定义工厂从 `ProbeConfig.prometheus` 和 `ProbeConfig.step_s` 取得实验配置。

## 时间窗口与证据边界

每个 ArmRun 保存发压时钟对应的 Unix 时间；请求的相对时钟和 warmup/hold/cooldown 划分保持不变。
指标查询在所有 Arm 的请求排空和 Runner cleanup 后执行，不占用 hold 时长。full 窗口
覆盖开始发压至 cooldown 结束，排除 setup/cleanup。统计窗口与执行阶段独立。

查询开始边界向上取整到毫秒，结束边界取窗口结束之前最后一个毫秒。Prometheus 的 range query
端点包含两端，PromQL range selector 仍遵守 Prometheus 自身语义。rate/increase 的滚动区间可能
读取压测开始之前的样本，increase 也有边界外推；结果是服务时间窗口估计，不能当作逐请求精确计数。

压测前应确认 Prometheus 已持续采集目标指标、label 选择正确且历史保留时间足够。配置地址不会
补回过去未采集的数据。查询结束时数据可能尚未入库，可在稍后独立补采；Perf 不通过重复发压弥补缺口。
成功的 HTTP 查询并不证明抓取覆盖完整，需要结合 `up`、样本时间和实例维度解释。共享服务可能混有
其它流量，没有请求归属标签时，不能声称指标全部来自本轮压测。

空结果、warnings、非有限值、超时与响应超限保存为查询错误和缺失，不伪造零值。原始结果与
warnings/infos 留在证据中，服务端 annotations 不丢弃。指标采集错误与请求结果、执行终态独立。

## 补采和离线报告

CLI 在查询前先保存请求事实和查询计划，查询被中断也可独立补采。Python 调用方可通过
`Engine.run(collect=False)` 先落盘，再调用 `Engine.collect_metrics(run)`；默认仍自动查询。

Run 保存每条查询的表达式、来源地址、Unix 起止时间、step、Operation 绑定、原始返回结果、
采集时间和错误。历史查询是 schema 11 的附加字段 `executions[].prometheus_observations`，
发压 Unix 起点为 `started_at_s`。趋势落入 timeseries.csv，汇总进入既有 MetricStore。

```bash
python -m perf_harness.cli collect-metrics runs/api/one-run --config experiment.yaml
python -m perf_harness.cli report runs/api/one-run
```

`collect-metrics` 只读取配置的 prometheus 段和 Run 保存的查询，不导入项目 Runner，不发压，不执行
部署或业务清理。省略 `--config` 时使用 Run 的 config.yaml 快照；鉴权环境变量仍需存在。
补采必须使用与原查询相同的来源 URL；查询语句和时间窗口以 Run 为准，不被新配置覆盖。

每次补采前后均向 `metric-collections/` 追加不可变查询记录，保留此前的失败证据。新的结果替换
Run 的指标投影，不能在本次查询失败时继续展示上次成功值。请求记录、Judge 结果和执行错误保持不变。
`report` 与 `analyze` 只读已保存事实，无远端 I/O；补采失败返回非零退出码，可稍后再次执行。

## Operation 与 Graph

Probe 的 `operation` 引用 Service 内的 Operation 名，由 Common 生成 qualified ID；指标通过
service/operation labels 与可选 `graph: product.graph.json` 关联。Graph 快照保存在 Run 中，
离线重载不再依赖项目文件。`prometheus_query` 的绑定仅描述归属，不改写自定义 PromQL。内置 `http_api` 根据服务、method、
route 自动生成过滤条件；两者均不证明某次实际调用。详见 [HTTP 指标与 Call Graph](callgraph.md)。

Query 的 `labels` 必须明确声明返回维度，不能覆盖已绑定的 service/operation。按实例查询时保留
instance 或 pod，服务级查询由 PromQL 显式聚合。Graph 中没有指标的节点仍是未知，不是零流量；
报告不沿静态 calls 关系自动累加延迟或断言瓶颈。

## 显式 Histogram summary

已有 `_bucket` 指标可声明原始桶查询；输出只按 `le` 分组，不能预先执行 histogram_quantile：

```yaml
summaries:
  - name: search_p95
    promql: sum by (le) (increase(search_duration_seconds_bucket[$window]))
    unit: s
    histogram: {quantile: 0.95, scale: 1}
    window: {kind: phase, phase: hold}
```

`scale` 在分位数计算后换算单位（毫秒转秒用 0.001）。HttpApiProbe 自动采用这一方式。
有桶证据的溢出分位数保存并显示为 `+Inf`；任意 scalar 查询不会猜测桶上限。
无样本仍显示缺失。`queries` 中普通 PromQL 趋势保留其查询本身的语义，
包括 histogram_quantile 的有限桶上界行为；不要将趋势点当成整窗分位数。
