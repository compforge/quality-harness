# perf_harness

把一个服务放到某个资源档下、施加压力、观察它随时间的表现，回答 **"在 xx 资源 + xx 压力下，
扛得住多少、表现如何"**。不测对错（`e2e_harness`）、不测回答质量（`eval_harness`）。

模型三行：

> **Experiment** 把资源档 × 负载档解析成命名 **Arm**；
> 每个 Arm 执行一次 **Trial**，计划用 Stage，观测按 **Window** 同时切请求与资源指标；
> report / SLO / analyze 都按 Arm + Window 查询同一份 metric 数据。

## 最短路径

```yaml
# my-exp.yaml —— 压一个已部署的服务，最小配置
name: example-sizing
extensions: [my_project.perf]            # import 后注册 workload / 自定义 probe
service:
  name: example
  component:
    repository: { forge: github, path: my_org/my_repo }
    name: api
  environment: { name: dev, kubeconfig: ~/.kube/config }
  base_url: http://example.default.svc:8000
  namespace: default
  k8s_selector: app.kubernetes.io/name=example   # 可选：开 k8s 资源观测

workload: { name: example, path: /api/v1/run, timeout: 180 }  # 你注册的协议适配（见下）

caseset: ./cases.yaml                    # canonical CaseSet：唯一持有 input/facets/judge
cases:                                   # 本实验只选 case + 配 weight
  - { id: simple,  weight: 70 }
  - { id: complex, weight: 30 }

load: { model: closed, levels: [5, 10, 20, 40], ramp_s: 20, steady_s: 120 }  # 容量扫描

observe:                                # 看哪些 Service（资源侧）
  - name: example
    probes:
      - name: prometheus                # Prombed 抓 /metrics 并执行 PromQL
        queries:
          - { name: request_rate, promql: "sum(rate(http_requests_total[1m]))", unit: req/s }
          - { name: active, promql: "sum(http_requests_active)", kind: gauge }
      - top
      - rss
      - limits
      - pods

cooldown_s: 60                          # 可选：停用后继续采样回收/缩容曲线

slo:                                    # 可选：run 级门 → CI 退出码
  - { metric: error_rate, lt: 0.01 }
  - { metric: 'p99_ms{difficulty="complex"}', lt: 30000 }
  - { metric: error_rate, window: {kind: hold}, lt: 0.01 }
  - { metric: 'prometheus.active{service="example"}.last', window: {kind: cooldown}, lte: 0 }
```

SLO 是 Service Level Objective（服务级别目标），即我们希望系统达到的、可以量化验证的目标。
默认在 measurement Window 求值；`window: {kind: hold}` 会对每个 hold 分别求值，
`window: {kind: cooldown}` 则读取 `deactivate()` 后的恢复窗口。Window selector 还可用
`name` / `level` 缩小范围。错误率熔断会使 run 失败；capacity 只读取已完整观测的 hold Window。

```bash
# 离线 smoke（无需服务/集群，内置 MockWorkload）：
cd sdks/python && uv run python -m perf_harness.cli run perf_harness/examples/mock.yaml
# 真实压测（在你自己的项目里，那里 register_workload 了你的适配）：
python -m perf_harness.cli run my-exp.yaml          # 非 pass 即非零退出（CI gate）
python -m perf_harness.cli analyze <run_dir>        # 确定性分析透镜（容量/资源/延迟/有效性）
python -m perf_harness.cli report  <run_dir>        # 从模型层重渲染报告（不重压）
```

产物落 `runs/<experiment>/<run_id>/`，累积不覆盖。三层分离——改报告版式不碰前两层：

```
outcomes.jsonl · timeseries.csv    # raw：每请求事实 / probe 采样
run.json                           # 模型层（schema 版本化）：分析的唯一入口，load_run 可离线重建
config.yaml · caseset.yaml        # 本次配置与 canonical CaseSet 快照（引用时）
report.md/html · summary.csv · by_facet.csv · windows.csv · verdict.json  # 视图层
```

## 配置面速查

| 顶层 key | 干什么 | 细节 |
|---------|--------|------|
| `service` | 压谁：Component + Environment + Service 身份，以及 `base_url`/`headers`/`namespace`/`k8s_selector` | 运行态目标 |
| `deployer` | 可选部署实现，例如 `{type: helm, …}` | 不写 = 直连已部署服务 |
| `resources` | 资源档列表（workers/cpu/memory/replicas），网格第一轴 | 无 deployer 时仅作标注 |
| `load` | 压多狠、怎么随时间变：`model`(open/closed) + `levels` 或 `stages` + pacing/熔断/优雅停 | [`docs/load-model-redesign.md`](docs/load-model-redesign.md) |
| `workload` | 协议适配器（你写、`register_workload` 注册；框架只内置 mock） | 见下「接入一个服务」 |
| `extensions` | 运行前导入的 consumer 模块；模块注册 workload / 自定义 probe | 配置和 CLI/SDK 使用同一发现方式 |
| `caseset` / `cases` | 引用 canonical CaseSet；`cases` 可选择/排序并设置实验本地 `weight`，省略则全选 | input/facets/judge 由 CaseSet 持有，实验不覆盖 |
| `cases` / `facets` | 无 canonical CaseSet 时仍可内联定义混合流量 | 报告按 facet 边际拆；`weight` 仍是实验用法 |
| `observe` | 资源观测：看谁（self+下游同一形状）、抓什么（prometheus/top/rss/restart/limits/pods 或自定义 probe）；prometheus 内声明 PromQL `queries` | Prombed 负责抓取/存储/查询；`service` 是 perf 的 label |
| `cooldown_s` | measurement 结束且 `deactivate()` 完成后继续采样的秒数；用于回收、缩容与泄漏曲线 | 原始 series 保留；`window: {kind: cooldown}` 显式读取 |
| `slo` | run 级断言；metric label 选实体，`window: {kind/name/level}` 选时间（三态 pass/fail/skipped） | [`docs/result-semantics.md`](docs/result-semantics.md) |

## 接入一个服务

1. 在**你自己的项目**里写 `Workload` 子类：`fire(case)` 发一个请求、回**原始** Outcome
   （不下结论）；协议特有的成败规则 override `judge(outcome)`（如 SSE "200 但流坏了"）。
   `stream_sse` 帮你发 SSE 并自动记 `ttft_ms`。
2. `perf_harness.register_workload("<name>", lambda cfg: YourWorkload(**cfg))`；需要 trial
   Trial 依次经历 `setup → measurement → deactivation → cooldown → cleanup`；
   consumer 只需实现 `setup` / `deactivate` / `cleanup`。
3. 在配置的 `extensions:` 写该模块名；需要业务观测源时用 `register_probe` 注册。
4. 复制 [`examples/example.yaml`](examples/example.yaml) 改 `workload.name` / `service` / `load`。

完整扩展契约、生命周期顺序和动态 Pod 曲线见
[`docs/extensions.md`](docs/extensions.md)。

## 进阶指针

结果可信度语义全部收敛在文档里，README 不展开——按需查：

- **加压模型**（open/closed、ramp≠warmup、max_inflight 防 CO、熔断与优雅停）→
  [`docs/load-model-redesign.md`](docs/load-model-redesign.md)
- **结果与 SLO 语义**（三层 verdict、三态 SLO、TrialStop、capacity 口径）→
  [`docs/result-semantics.md`](docs/result-semantics.md)
- **metric 模型与寻址**（family/series/label、side、caveats、Missing、SLO 引用合法性）→
  [`docs/metric-model.md`](docs/metric-model.md)
- **consumer 扩展**（模块发现、trial hooks、自定义 Probe、cooldown、Pod 数量）→
  [`docs/extensions.md`](docs/extensions.md)
- **框架开发者视角**（代码地图、扩展点、约定）→ [`AGENTS.md`](AGENTS.md)
