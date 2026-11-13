# HTTP 指标与 Call Graph

一次实验通常使用一个 case。项目维护该 case 的正常调用路径，以实验的 `graph` 配置引用；
Harness 不根据节点可达性猜测业务分支。不同 case 的路径分别声明，case 内条件调用标记为
`may_call`，正常必经调用用 `calls`（图上显示 `call`）。Graph 仍是静态声明。

## HTTP 指标关联

Common 已有 `HttpOperation(name, service_name, method, path)`，其中 path 使用路由模板，
如 `/faqs/{dataset_id}/retrieve`，不填写某次请求的具体 dataset ID。Operation ID 保持稳定，
service + method + route 用于选择 Prometheus 指标；相同 path 的不同服务或方法不会混合。

```yaml
service: {name: entry, base_url: 'http://entry:8080', environment: {name: qa}}
runner: {name: http, method: POST, path: /ask}
cases: [{id: faq, input: {question: example}}]
graph: ./faq.graph.json
load: {request_rate: 8, max_inflight: 200, hold_s: 60}
prometheus: {url: 'http://prometheus:9090', step_s: 15}
observe:
  - name: knowledge
    http_metrics:
      requests_metric: http_server_request_duration_seconds_count
      duration_bucket: http_server_request_duration_seconds_bucket
      service_label: service_name
      method_label: http_request_method
      route_label: http_route
      match: {namespace: qa}
      duration_unit: s
      qps_semantics: completed
      rate_interval: 5m
    probes:
      - name: http_api
        operation:
          name: retrieve
          method: POST
          path: /faqs/{dataset_id}/retrieve
```

指标名与 label 需依据实际导出结果配置；示例标签不是所有 OTel/Prometheus 部署的固定保证。
profile 每个 Service 声明一次。默认 service_label=service_name、method_label=http_request_method、
route_label=http_route。`service_value` 可覆盖实际服务标签值；`match` 补充集群、namespace、租户等
等值过滤，但不能覆盖 service/method/route。Prometheus 未导出某标签时，应先调整匹配配置，
不能自动删除过滤条件扩大查询范围。

服务端将模板导出为 `/faqs/:dataset_id/retrieve` 等不同写法时，可在 probe 上显式设置
`metric_route`。SDK 不把任意 URL 正则归一化，不根据 raw path 猜模板。

使用 HTTP server 指标。requests_metric 应是单调请求计数器；默认 QPS 表示完成事件，
若计数器在请求进入时增加，应配置 `qps_semantics: entered`。SSE 完成计数会滞后于发起速率，
HTTP 完成和 HTTP 200 也不能替代业务终态 Judge。

Python 使用 `HttpMetricProfile` 和 `HttpApiProbe`，从 `perf_harness.observe` 导入。
特殊协议或已有 PromQL 仍可使用 `PrometheusQueryProbe`，声明
`operation_metrics={"qps": "<summary name>", "p95": "<summary name>"}`：必须指向无额外结果 label 的
WindowQuery，单位分别为 rps、s。QPS/p95 均可独立省略，至少绑定一项，未绑定的角色显示 N/A。
OperationMetricBinding 将角色引用和各自的口径说明关联到 Operation，使用 `operation_metrics` 配置/落盘字段。
每个角色从对应 WindowQuery 的 description 继承口径，各窗口声明同一角色时说明须一致。
例如 QPS 可以来自 Counter，p95 来自独立的日志 Histogram；来源不同是正常情况。
说明应包含测量边界、统计范围及覆盖限制，随 Run、callgraph.json 和节点详情保存。
不能把两种来源的计数差异直接解释成精确覆盖率或丢失率。
中间件可用普通 Operation 归属于 Service，只绑定已有 QPS；不必虚构 HTTP method/path 或耗时分位数。多个互不重叠的窗口 kind 可复用同一个 summary 名。

## SSE Operation

Common `SSEOperation(name, service_name=..., method="POST", path=...)` 继承 HttpOperation，
共享 HTTP 指标的 method/route 匹配，但用 `response_mode=sse` 区分流式响应。
普通 HttpOperation 为 `response_mode=unary`，Operation ID 不受模式影响。
`http_api` 的 operation 配置可写 `response_mode: sse`；省略时为 unary。
自定义 Runner 可从 `operation(ctx)` 返回 SSEOperation；Run schema 10 重载会保留具体类型。
OperationMetricBinding、callgraph.json 和节点详情保留响应模式；也可读取静态节点 properties 中明确声明的 response_mode；缺少声明时不推测模式。

该类型声明不改变请求调度、timeout、Judge 或 histogram 计时口径。SSE 的 HTTP 200 不能
替代业务终态，首个 SSE 事件也不一定是有效输出。分析 p95 时核对对应指标测量的是
服务端 middleware、首个有效输出还是完整流，不能仅凭 SSE 类型重新命名或换算指标。

## 时间窗口与数据

自动查询包括 QPS/p95 趋势和 full、warmup/hold/cooldown 阶段与自定义范围汇总。
趋势采用 `rate_interval` 滚动区间；`step_s` 只决定求值步长。整窗 QPS 使用该窗口长度的 rate，
p95 先合并全部匹配实例的 histogram bucket，再计算 quantile；不能平均 Pod p95 或趋势 p95。
`duration_unit: ms` 会将结果转换为秒。当前生成器适配 classic histogram，其他类型可用自定义查询。

短于抓取间隔的窗口可能没有足够样本；空结果、非有限值和查询失败保留为 N/A 与查询诊断。
真实零计数速率显示 0。低流量下 histogram_quantile 可能是 NaN；边界 bucket 不足也会限制
延迟估计，需要结合服务指标的 bucket 配置判断。

Run 保存 graph 快照、case_ids、operation_metrics 指标地址，以及已有的原始查询和结果。
指标始终按 Operation ID 关联，图中未配置指标的节点仍保留为 N/A。共享环境中 metrics
可能含其他流量；混合 case 的图明确显示 aggregate scope，不将聚合值分配给单个 case。

## 离线报告

`report.html` 将原有汇总、吞吐和资源图表放在默认的 Report Tab，调用图放在独立 Callgraph Tab，使用页面全宽和随视口高度变化的画布。
Callgraph 下方的「指标明细」默认折叠，与图共用 Arm/Window 选择，保留该窗口全部 Operation
指标的统计、单位和来源；Report Tab 不再重复显示 API 观测表。
无 graph 的报告保持full API 观测表。Callgraph 只画 Operation 节点与 calls/may_call 边；Service 显示为分组背景；节点悬浮提示 service/path，节点正文只显示 Operation 与指标
和颜色。支持按 Arm/Window 切换、缩放、拖动画布、Fit、点击节点查看指标地址、PromQL 和查询错误。
图右下角的 0–100% 滑块调整缩放；100% 为原始大小，0 端使用最小倍率 0.1%，
滚轮和 Fit 会同步更新百分比。隐藏页面首次打开时才按可见画布布局。
「复制图片」将当前 Arm/Window 的完整图复制为白底 PNG，不受当前缩放和可见区域限制，最多 2 倍绘制、最长边 4096 像素；
导出像素不再叠加屏幕 devicePixelRatio，避免 Retina 下生成超大剪贴板位图。
浏览器不支持图片剪贴板或拒绝权限时显示失败原因，不会报告复制成功。
默认选 full；先选全程/阶段/自定义范围，再选具体范围。`may_call` 下游节点出现流量是使用线索，不能在缺少 caller 维度时标记该边已执行。

使用 Cytoscape.js + Dagre 自动布局，资源与 ECharts 一并内置，生成及查看不依赖 CDN。
`python -m perf_harness.cli report <run_dir>` 只消费落盘事实；指标补采继续使用 collect-metrics，
不会触发重新压测。Markdown 报告提供 HTML 图入口。

## 分析顺序

1. 查看发起速率、按 code 完成吞吐、在途请求和延迟，分清加压阶段与 drain。
2. 在同一窗口沿图查看有流量的下游节点、延迟变化、响应结果及观测缺口。
3. 对比各档负载的时间序列，结合所属 Service 的 CPU、内存、重启等证据缩小范围。
4. 用 trace、日志或只改变一个条件的实验确认因果。最大 p95 不自动等于瓶颈；路径上的 p95
   不能相加，也不能用上下游分位数相减得到本地耗时。

SDK 保存事实与可复现视图；Skill/分析者负责解释证据，不引入 SLO 或自动瓶颈判定。

## Call Graph Snapshot

`callgraph.json` 是带指标的静态调用图派生产物（schema_version=2），不依赖 Cytoscape 格式。
顶层保存 Run、环境、case、图来源/版本和声明的调用边；arms 保存负载、资源与时间窗口。
每个窗口的 nodes 复用 Common Node，`properties.perf` 包含所属 service、HTTP method/path、
QPS 计数口径以及 metrics。每个 metric 保存数值、单位、observed/missing/error、MetricStore
地址与查询来源。真实 0 保留为 0，缺失/失败使用 null 并记录原因；不会用 NaN 代替。

静态 Node/Relation 的其它 properties 原样保留，Perf 只拥有派生节点的 `perf` 命名空间。
原始 product.graph 不修改，属性也不参与 ID。图只包含 Operation；Service 是属性信息。
HTML 从同一快照数据渲染，Skill/Agent 可以直接读取 JSON 作参考，不必从展示文本反推数值。

跨轮比较前须对齐 case、目标版本、graph、资源、负载及窗口和指标口径。数据补采后重建快照
反映最新观测；比较时应保留使用过的快照和采集尝试。该产物不声明业务达标或自动定位瓶颈。
