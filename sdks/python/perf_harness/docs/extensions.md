# 扩展与 Trial 生命周期

perf harness 只负责负载编排、观测和结果模型；服务协议、环境准备与业务指标留在 consumer
项目。consumer 通过一个可导入的 Python 模块注册扩展，experiment 配置显式声明该模块：

```yaml
extensions: [my_service.perf]
workload: { name: my-service }
```

`extensions` 是 run 配置的一部分，因此同一份配置经 CLI 或 `load_experiment()` 运行时都会加载
相同扩展；模块需要在当前 Python 环境的 import path 中。

## Workload 与生命周期

`Workload.fire()` / `judge()` 负责单次请求。`TrialContext` 是整个 Trial 共用的不可变输入，
`FireContext` 在它之上组合本次 dispatch 的 Case；一次 Trial 前后的有状态操作使用三个可选 hook：

```python
from perf_harness import FireContext, Outcome, TrialContext, Workload, register_workload


class MyWorkload(Workload):
    async def setup(self, ctx: TrialContext) -> None:
        ...  # 创建本 trial 所需的外部状态

    async def fire(self, ctx: FireContext) -> Outcome:
        response = await ctx.trial.client.post(
            ctx.trial.service.base_url + "/chat",
            json=ctx.case.input,
            headers={"x-perf-run-id": ctx.trial.run_id},
        )
        return Outcome(status=response.status_code, duration_ms=...)

    async def deactivate(self, ctx: TrialContext) -> None:
        ...  # 触发停止/缩容；此时 Probe 仍在采样

    async def cleanup(self, ctx: TrialContext) -> None:
        ...  # 最终清理；Probe 已停止，HTTP client 仍可用


register_workload("my-service", lambda cfg: MyWorkload())
```

`setup`、所有并发 `fire`、`deactivate` 和 `cleanup` 看到的是同一个 `TrialContext`；每次
`fire` 的 `FireContext` 则是独立对象。不要把当前 Case 写回 TrialContext，否则并发 dispatch
会共享并覆盖请求状态。取消与 deadline 沿用 asyncio task / timeout 语义，不塞进领域 Context。

顺序固定为：

```text
setup → measurement → deactivation → cooldown → cleanup
```

顶层 `cooldown_s` 控制停用后的观测窗口。cooldown 样本进入 `run.json` /
`timeseries.csv` 和 HTML 曲线，用于回收、缩容与泄漏观察；Trial 汇总和默认 SLO
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

`limits` 同样每周期刷新 Pod 集合，因此动态扩缩容时聚合 request/limit 不会停留在 trial
开始时的副本数。`client.sent` counter 在报告中转换为逐 tick 实际发送速率，可与 Pod 数量及
业务 gauge 对齐查看。
