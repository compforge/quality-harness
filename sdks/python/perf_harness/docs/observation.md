# Graph、Observation 与 Experiment

项目维护静态调用关系和观测声明，实验引用它们并指定环境及负载。
Engine 按 Experiment 的计划调用 Runner 执行 case，组织观测、归约和落盘；
报告将已保存的观测结果关联到 Graph。瓶颈分析由使用者或 perf skill 完成。

| 声明 | 内容 | 所属层 |
|---|---|---|
| Graph | Service / Operation、归属与 calls / may_call | Common Graph；项目维护拓扑 |
| ObservationSpec | 观察哪个对象、哪些指标、由哪些 Probe 提供 | perf |
| Experiment | case、环境、负载、执行保护、请求通道与统计窗口 | perf；复用 Common 执行和访问契约 |

ObservationSpec 是观测意图，不能与已经取得的 Observation 事实混淆。
Service、Operation / HttpOperation / SSEOperation、Workload / KubernetesWorkload 均复用 Common。
Workload 的名字在 Service 内限定范围，不额外发明 infra ID；Kubernetes 实例发现由 toolbox
resolve_workload 完成。数据源使用 toolbox PrometheusDataSource，客户端由 Common ClientManager
复用与回收，perf 不实现另一套连接池、发现或抓取系统。

## 项目文件与实验装配

```text
perf/
  product.graph.yaml
  observations.yaml
  experiments/qa.yaml
```

实验中的路径相对于实验 YAML 文件。Graph 使用 Common schema 1，支持 YAML 或 JSON；
项目文件可省略 environment，由实验绑定后形成完整 Common Graph 快照；显式声明时必须匹配环境。
Operation 节点的 method、path、response_mode 放在 properties 中。

```yaml
# experiments/qa.yaml
name: search-capacity
service:
  name: search
  base_url: http://search.qa:8080
  environment: {name: qa}
runner: {name: http, method: POST, path: /search}
cases: [{id: example, input: {query: example}}]
graph: ../product.graph.yaml
observation: ../observations.yaml
data_sources:
  metrics: {type: prometheus, url: 'http://prometheus.qa:9090', step_s: 15}
observation_windows:
  - {kind: full}
  - {kind: phase, phase: hold}
load: {request_rate: 8, max_inflight: 200, hold_s: 120}
```

`observation` 可引用文件，也可内联同样的 mapping。`services` 列表提供下游 Service 的环境和
Workload 声明，环境默认继承被测 Service；root Service 不重复声明。Graph 中仅用于 Prometheus
查询的 Service 可省略运行承载配置。没有 Graph 时，在 observation 资产的 `operations` 列表
声明 Common Operation 字段（name、service_name，可选 method/path/response_mode）。

`data_sources` 用逻辑名关联 Probe，不把环境地址写入项目查询模板。
Prometheus 的 headers_env、timeout_ms、连接池与结果大小限制复用已有访问配置。
`observation_windows` 默认 full / phase / custom；它选择求值窗口，不改变执行阶段或请求耗时。
同一指标声明一次，在所选窗口求值；窗口规则不能重复或重叠。

## 观测内容与获取手段

```yaml
# observations.yaml
observations:
  - target: operation:search/search
    probes:
      - type: prometheus_query
        data_source: metrics
        metrics:
          qps:
            promql: sum(rate(search_requests_total[$window]))
            unit: rps
            semantics: completed requests
          p95:
            promql: sum by (le) (increase(search_duration_seconds_bucket[$window]))
            histogram: {quantile: 0.95}
            unit: s
            semantics: complete request duration
          error_rate:
            promql: sum(increase(search_errors_total[$window])) / (sum(increase(search_requests_total[$window])) > 0)
            unit: ratio
            semantics: business failures / completed requests
```

指标名称是开放字符串。RED 中 qps / error_rate / p95 是当前选取的代表性属性，单位约定为
rps / ratio / s；不会因此限制其他观测。错误率保存 0–1，显示百分比。每个指标保留 description
和 semantics。项目负责查询的实际服务、路由、环境和业务过滤；target 只建立身份关联，
不会替自定义 PromQL 自动注入 selector。错误 counter 无序列的含义由埋点契约决定，不能无条件补零。

同一 target 可以有多个 Probe，使用 `name` 区分相同 type 的实例。贡献不同指标时合并；
同一 target/workload 的同名指标生产者冲突在发压前报错。一个带多个结果 label 的查询不能
直接绑定为节点标量；显式聚合后再声明指标。`queries` 可继续声明独立趋势查询。

HTTP 通用 profile 使用 `type: http_api`、Operation target、`data_source` 和 `profile`。
profile 自动提供 qps/p95；只有同时配置 status_label 和 error_status_pattern 时才提供 HTTP
错误率。分母仅包含非空 status 的请求序列，无流量或缺少 status schema 保留 Missing。
HTTP status 错误率不代表 SSE 业务错误率；后者需要项目查询和对应业务 counter。

资源观测不必转换成 RED。例如观察 Common Service 中已声明的 Workload：

```yaml
observations:
  - target: service:search
    workload: workers
    probes:
      - type: restart
        metrics:
          restarts: {metric: restarts.increase}
      - type: top
        metrics:
          cpu_peak: {metric: cpu_m.peak}
          memory_peak: {metric: mem_mi.peak}
```

`top/rss/restart/limits/pods` 沿用已有 Probe。metric 显式选择该 Probe 输出的 family/stat；
不配置 metrics 也会保存 Probe 输出。per_pod 输出需要按 Pod 查看，不暗中生成 Service 汇总。
自定义类型继续使用 register_probe 和 ProbeConfig，可获取已解析的 Operation、Workload 与数据源。
Python 可直接构造 ObservationSpec(service, probes, operation=..., workload=...)，交给
Experiment.observation；独立 Probe 调用仍可使用 Experiment.probes，二者不混用。

## 执行与事实

加压器自身及每次请求的 Outcome / Judge 数据自动记录，无需声明一个虚假的请求 Probe。
周期 Probe 在执行期间采样；历史查询在实际窗口确定后执行。Engine 只调度观察过程，
Prometheus 查询计划和转换保留在 observe 适配层。

Run schema 11 保存 graph 快照、observation_bindings、指标类型及原始查询/结果。
ObservationBinding 以目标身份、可选 workload 和开放 metrics 映射指向 MetricStore；
不保存第二份指标值，不改写静态节点。旧 schema 不兼容读取。
报告离线读取这些事实；collect-metrics 使用实验的具名 data_sources 和已保存查询补采，
不加载业务 Runner、不发压，不依赖源 Observation 或 Graph 文件。

缺失、查询错误、数值零和 histogram 溢出的 +Inf 分开表达。查询失败保留证据并降低观测完整性，
不修改请求结果或自动裁决业务是否达标。静态边与节点流量不构成单次请求路径证明。
