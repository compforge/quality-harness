# 扩展与 ArmRun 生命周期

perf harness 只负责负载编排、观测和结果模型；服务协议、环境准备与业务指标留在 consumer
项目。consumer 通过一个可导入的 Python 模块注册扩展，experiment 配置显式声明该模块：

```yaml
extensions: [my_service.perf]
runner: { name: my-service }
```

`extensions` 是 run 配置的一部分，因此同一份配置经 CLI 或 `load_experiment()` 运行时都会加载
相同扩展；模块需要在当前 Python 环境的 import path 中。

## Runner 与生命周期

`Runner.fire()` 负责单次请求；独立 `Judge` 负责原始证据的纯判定。`ArmContext` 是整个 ArmRun 共用的不可变输入，
`FireContext` 在它之上组合本次 dispatch 的 Case；一次 ArmRun 前后的有状态操作使用三个可选 hook：

```python
from perf_harness import FireContext, Outcome, ArmContext, Runner, register_runner


class MyRunner(Runner):
    async def setup(self, ctx: ArmContext) -> None: ...  # 创建本 arm_run 所需的外部状态

    async def fire(self, ctx: FireContext) -> Outcome:
        response = await ctx.arm_run.client.post(
            ctx.arm_run.service.base_url + "/chat",
            json=ctx.case.input,
            headers={"x-perf-run-id": ctx.arm_run.run_id},
        )
        return Outcome(status=response.status_code, duration_ms=...)

    async def deactivate(self, ctx: ArmContext) -> None: ...  # 触发停止/缩容；此时 Probe 仍在采样

    async def cleanup(
        self, ctx: ArmContext
    ) -> None: ...  # 最终清理；Probe 已停止，HTTP client 仍可用


register_runner("my-service", lambda cfg: MyRunner())
```

`setup`、所有并发 `fire`、`deactivate` 和 `cleanup` 看到的是同一个 `ArmContext`；每次
`fire` 的 `FireContext` 则是独立对象。不要把当前 Case 写回 ArmContext，否则并发 dispatch
会共享并覆盖请求状态。取消与 deadline 沿用 asyncio task / timeout 语义，不塞进领域 Context。

顺序固定为：

```text
setup → warmup → hold → cooldown → cleanup
```

顶层 `cooldown_s` 控制停用后的观测窗口。cooldown 样本进入 `run.json` /
`timeseries.csv` 和 HTML 曲线，用于回收、缩容与泄漏观察；ArmRun 汇总和默认 SLO
仍只统计 measurement，只有显式 `window: {kind: cooldown}` 的资源 SLO 读取 cooldown。
即使 setup 或 measurement 抛错，`cleanup` 仍会执行。

## 自定义 Probe

框架内置通用的 `prometheus/top/rss/restart/limits/pods`。Prometheus 指标直接在内置
probe 上声明 PromQL；其它业务专用来源才需要注册 Probe factory：

```python
from perf_harness import FamilySpec, Probe, ProbeConfig, register_probe


class QueueProbe(Probe):
    name = "queue"
    source = "http"
    families = {"depth": FamilySpec("count", "gauge", "queued jobs")}

    def __init__(self, cfg: ProbeConfig):
        self._service = cfg.service
        self.name = f"{self.name}.{cfg.service}"
        self.path = str(cfg.options["path"])

    async def sample(self, ctx):
        response = await ctx.probe_client.get(ctx.service.base_url + self.path)
        response.raise_for_status()
        return {"depth": float(response.json()["depth"])}


register_probe("queue", QueueProbe)
```

配置中的 mapping 除 `name` 外都会进入 `ProbeConfig.options`：

```yaml
observe:
  - name: worker
    probes:
      - { name: queue, path: /debug/queue }
```

自定义 Probe 和内置 Probe 使用同一 `FamilySpec`、series label、汇总、SLO 与报告链路；不要在
factory 中写 load 编排或业务判定。

## 动态 Pod 数量

`pods` 根据 observe entry 的 `namespace + k8s_selector` 每个采样周期读取当前 Pod 集合，产出：

```text
pods.count{service="worker",state="total|active|ready|running|pending|unschedulable|terminating"}
```

`limits` 同样每周期刷新 Pod 集合，因此动态扩缩容时聚合 request/limit 不会停留在 arm_run
开始时的副本数。`client.sent` counter 在报告中转换为逐 tick 实际发送速率，可与 Pod 数量及
业务 gauge 对齐查看。

## Environment Host

`service.environment` 使用 common 的 Environment / Host 格式。省略 `host` 时在当前主机
执行 Kubernetes 操作；声明 SSH Host 后，Kubernetes 观测与 Helm 操作在该 Host 执行。`context` 同时用于 kubectl 与 Helm，未声明 environment 的下游观测项继承
主服务环境；显式 `host: null` 恢复当前主机访问。

```yaml
service:
  environment:
    name: dev
    kind: kubernetes
    host: {name: devbox, transport: ssh, address: my-devbox}
    kubeconfig: /home/dev/.kube/config
    context: dev
```

远端 kubeconfig、chart 和 values 路径属于 Host，应使用 Host 上的绝对路径；框架不会在
Runner 上展开远端路径或复制文件。Host 不改变压力机位置，Chat/HTTP/SSE 以及 Prometheus
请求仍从 Runner 发出，`base_url` 和 probe URL 必须从 Runner 可达。仅 HTTP 的 generic/host
环境可以发压，但 Kubernetes probes 在没有集群访问配置时不产出观测。


### 原生 Pod 观测

`restart/limits/pods` 通过 toolbox 原生 Kubernetes API 读取 Pod manifest。安装时启用
`quality-harness[kube]`；SSH Host 还需在 `python3` 环境安装相同版本的 `harness-toolbox[kube]`。
SSH 使用共享资源 worker 的只读视图，Kubernetes 客户端在 Host 上读取 kubeconfig，ArmRun 结束后关闭。
`top/rss` 使用 kubectl 的指标与 exec 通道；Helm 部署仍要求 Host 上有 Helm。

每次 ArmRun 的观测期复用连接池（每个访问配置/namespace 最多 8 个连接，单次请求预算 10 秒）。
每个采样周期创建 toolbox `DataLoader`，让同一访问配置、namespace 和 selector 的三个探针共享 Pod 列表及读取错误；
下个周期重新读取，因此扩缩容不会沿用旧副本集合。列表读取失败进入 probe error 和 `up=0`，
不会记为零副本或零资源。SSH 请求超时或取消后关闭对应 worker；本次读取报错，后续采样新建通道，避免误读残留响应。

`observe_loop` 管理 `ProbeContext.clients` 的释放；直接调用 `Probe.sample` 的扩展测试，
需要自行使用 `async with ctx.clients` 管理该生命周期。

### Prometheus 观测

`PrometheusProbe` 通过 toolbox `PrometheusDataSource` 抓取 `/metrics` 并查询内嵌 Prombed。
同一访问配置的探针在单轮 `DataLoader` 内共享一次抓取，PromQL 查询仍各自执行。连接池与
有界查询历史由该 ArmRun 的 `ClientManager` 管理，新 ArmRun 不读取旧 ArmRun 的样本。
Prometheus 使用独立 HTTP 池，不占用发压连接；地址、认证和容量属于 DataSource，
采样频率、输出指标/label 契约以及 SLO 属于 perf。

## 独立 Judge

```python
from perf_harness import RequestEvaluation, register_judge


def chat_completed(outcome):
    ok = outcome.status == 200 and outcome.meta.get("saw_done") and not outcome.meta.get("exc")
    return RequestEvaluation(bool(ok), None if ok else "incomplete_stream")


register_judge("chat-completed", chat_completed)
```

配置 `judge: chat-completed`，或直接传 `Experiment(judge=chat_completed, ...)`。
Judge 不访问资源 Probe、不触发网络调用、不修改 Outcome。共享 `stream_sse` 来自 toolbox，
perf 包只适配返回类型；它记录 `first_byte_ms`（首字节），业务首 token 需要 Runner 显式识别后记录。
