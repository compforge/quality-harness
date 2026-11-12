# 请求连接通道

默认直连 `service.base_url`，忽略 shell 中的代理环境变量。`request_transport` 可显式选择
通道；它只影响 Runner 使用的 HTTP client，观测和 `/metrics` 的通道独立配置。

```yaml
service:
  name: chat
  base_url: http://10.0.0.10:8001
  environment:
    name: qa
    kind: kubernetes
    kubeconfig: /path/to/kubeconfig
    context: qa-context
request_transport:
  kind: pod-relay
  workload:
    name: perf-relay
    namespace: quality
    location: {kind: service, name: utility}
    container: app
  startup_timeout_s: 30
  connect_timeout_s: 10
  max_connections: 128
  max_targets: 16
  max_candidate_pods: 3
  heartbeat_s: 5
  lease_s: 30
load: {request_rate: 8, max_inflight: 100, hold_s: 60}
```

以上是连接配置片段，Case、Runner、资源和观测使用项目已有声明。压力机须能通过本机
kubeconfig 访问 Kubernetes API，并安装 `kubectl` 与 `quality-harness[kube]`。relay 使用
显式 Workload 中已有的 Running/Ready 容器，要求 Python >=3.8，不创建额外 Pod。
`max_connections` 必须覆盖所有 LoadPlan/Stage 的峰值 `max_inflight`。

路径为 `压力机 → 本次 Run 拥有的 port-forward → Pod relay → Service ClusterIP`。
`service.base_url` 保持业务逻辑地址，不能替换成某个 Chat Pod 的 loopback 地址；连接层由
Service 选副本，keep-alive 可复用连接，不能承诺请求在副本间均匀分配。

Python API 使用 `Experiment(request_transport=PodRelayTransport(...), ...)`，也可注入其它
实现 `ClientProvider[HTTPRoute]` 的通道。省略配置或 `{kind: direct}` 使用直连。
Runner 的 setup/fire/deactivate/cleanup 共用所选 client 和通道，直到最终清理后才关闭。
若通道初始化失败，Run 记录 setup phase error 和未完成的测量窗口，不调用 Runner。
运行中断线由 HTTP/Runner 记录原始失败，不自动重发请求或回退到 VPN。

原始配置随 CLI 原生产物留存。需将运行时选中的 relay Pod UID/container/helper PID 和清理
证据随运行保留；Toolbox 的 `PodRelayClient.diagnostics` 可提供快照，日志记录 ready/failed/closed。
relay 的资源消耗、Kubernetes API/port-forward 通道和压力机本身都可能影响吞吐；本机测试
只能证明转发与生命周期正确，实际容量需在声明的 QA 环境验证。

通道机制、默认预算与故障后的租约边界见 [Toolbox Pod relay](../../toolbox/docs/pod-relay.md)。
