# perf_harness

对服务施加受控负载，观察吞吐、延迟和资源表现。资源档与 LoadPlan 组成 Arm；每次 ArmRun
组织一批 Case 实际产生的 OperationRun。Case 资产复用 spec-case，执行事实复用 harness-common。

## 最短路径

```yaml
name: chat-capacity
extensions: [my_project.perf]
service:
  name: chat
  environment: {name: dfx}
  base_url: http://chat-server:8000
runner: {name: chat}
judge: chat-completed
cases:
  - {id: cao-cao, input: {question: 介绍下曹操}}
load:
  request_rate: 4
  max_inflight: 32
  hold_s: 60
  cooldown_timeout_s: 180
```

consumer 在 `my_project.perf` 注册 Runner 和可选独立 Judge。Runner 完整消费一次响应；SSE 的
HTTP 200 不等于业务完成，Judge 应读取 Runner 保存的完成事件。框架默认 Judge 按传输与状态码区分成功、失败、拒绝、超时；缺少判定单列。

`request_rate` 与 `max_inflight` 独立：满在途暂停发起，有空位按目标速率补充，不排队；`inf` 表示按并发补充。
`load` 可为配置列表，与 `resources` 展开网格。资源档在未配置 Deployer 时只作标注。
可用 `caseset: ./cases.yaml` 引用 canonical CaseSet，再用 `cases: [{id: ..., weight: ...}]` 选择。

```bash
# 本机离线 smoke，不需要被测服务
uv run python -m perf_harness.cli run perf_harness/examples/mock.yaml --out /tmp/perf
# 已授权目标上的真实实验
python -m perf_harness.cli run my-exp.yaml
python -m perf_harness.cli analyze <run_dir>
python -m perf_harness.cli report <run_dir>
# 独立补采外部 Prometheus 指标，不发压
python -m perf_harness.cli collect-metrics <run_dir> --config my-exp.yaml
```

每次运行写入 `runs/<experiment>/<run_id>/`：

| 产物 | 内容 |
|---|---|
| run.json | schema 9：Execution、Arm、Window 与归约结果 |
| requests.jsonl | 调度记录与关联 OperationRun 的唯一原始 Outcome |
| evaluations.json | 按 OperationRun ID 保存的独立请求判定 |
| timeseries.csv | Probe 原始采样 |
| report.html / report.md / lifecycle.csv | 报告与请求生命周期统计 |
| verdict.json | 执行诊断；无业务验收策略时为 skipped |

CLI 打印 HTML 路径，不自动打开。`load_run` 可离线加载事实；更换 Judge 后可用 `reduce_requests`
重算窗口、Case 与 facet 的请求统计，不会发出新请求。单纯重渲染报告不重判。

资源观测通过 `observe` 配置 Kubernetes 或项目 Probe。外部 Prometheus 地址由 YAML 顶层
`prometheus.url` 配置，服务指标在压测后查询，Perf 不自行抓取 `/metrics`。停用后观察可设 `cooldown_s`。
详细配置与边界见 [负载模型](docs/load-model-redesign.md)、[扩展](docs/extensions.md)、
[指标模型](docs/metric-model.md)、[结果语义](docs/result-semantics.md)。

Run.status 只描述 completed / stopped / error；请求失败不会直接判定实验失败。
吞吐按实际完成时间分窗，成功、失败、拒绝、超时和未判定互斥计数。
下游 API 指标绑定及示例见 [服务指标](docs/service-metrics.md#operation-与-graph)。
