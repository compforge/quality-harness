# Experiment 执行与产物

公共层统一执行后的事实与产物；领域 Engine 拥有调度、调用和生命周期。
事实的复评模型见 [Dataset 与复评](evaluation-data.md)。

## 执行对象

| 概念 | 语义 |
|---|---|
| **Experiment** | 一份具名、可复现的验证意图；公共层只拥有稳定名称，各 Harness 子类增加 Case、Arm、Workload、Metric 或 Policy 等领域配置。 |
| **ExperimentRun** | Experiment 的一次真实执行，以 experiment name、`run_id` 和创建时间标识；领域实现可以简称为 `Run`。 |
| **Execution** | ExperimentRun 内由领域定义的组织单元；E2E 的 CaseRun 与 perf 的 ArmRun 都是 Execution。公共层不规定调度器或 runner 的调用协议。 |
| **OperationRun** | 对某个 Service 的 Operation 的一次真实调用；直接拥有这次调用产生的 Outcome。 |
| **Outcome** | OperationRun 产生的原始领域证据。公共层只统一语义角色，具体字段由领域子类定义。 |
| **Reducer** | 只读取 ExperimentRun 已记录事实并产出 Artifact 的领域归约器；不得再次调用被测 Service。 |
| **Artifact** | ExperimentRun 产生的一份具名、可持久化产物；内容和 schema 归领域所有，公共层只记录逻辑名称与 run 目录内相对路径。 |
| **Report** | 一个或多个 Artifact 的面向人渲染，不重新执行 Experiment，也不重新生成源事实。 |
| **Verdict** | 对一次 Run 的可机器消费判定。人、CI 和 agent 开发循环都通过它判断是否通过、为何失败，以及下一步应读哪些证据。 |

## Experiment 执行骨架

```text
ExperimentRun
  └─ Execution × N
       └─ OperationRun × N
            └─ Outcome

Reducer.reduce(ExperimentRun) → Artifact × N → Report / Verdict
```

OperationRun 是一次真实服务调用的基本单位。ExperimentRun 通过领域自己的 Execution 组织这些调用，
而不直接管理所有调用细节：E2E 可以按 CaseRun 分组，perf 的 ArmRun 则组织固定 Arm、目标 Service/Workload
和一批 Case 在一次加压过程中产生的 N 次调用，N 由加压器决定。简单 Case 的一次触发对应一个 OperationRun；
多步骤 Case 可以产生多个 OperationRun。

公共层统一的是执行后的事实，不统一执行机制：领域 Engine 可以使用 runner、scheduler、队列或其它方式
组织 OperationRun。因而 common 不提供 `Driver.run` / `DriverRun`。Outcome 与 Artifact 也不是一一对应：
一个 Artifact 可以汇总大量 Outcome，同一批 Outcome 也可被多个 Reducer 投影成不同 Artifact。公共 Artifact
不枚举 Outcome ID；需要精确 provenance 时，由领域使用 Execution identity、selector 或时间窗口表达。

## 判定与执行状态

Verdict 是领域结果的机器投影；是否存在验收策略由领域契约决定。Perf 只报告执行状态与请求观测，
不输出业务通过结论，见 [Perf 契约](../spec/perf-contract.md)。通用状态与证据结构见
[Verdict schema](../spec/verdict-schema.yaml)。
