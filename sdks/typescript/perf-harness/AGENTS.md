# TypeScript perf-harness

## 项目定位与边界

本包是 Perf Harness 跨语言契约的 TypeScript 实现，供 Doctor 等 TypeScript 消费方在不依赖
Python runtime 的环境中制造受控负载并保存请求侧性能事实。Python 与 TypeScript 是并列实现；
公开名词、调度语义和 `run.json` / `outcomes.jsonl` 字段共同遵守 `spec/` 契约。

第一阶段只拥有负载调度、Workload 扩展、请求侧 Outcome、Window 归约和 raw/model 落盘。
Prometheus、Kubernetes 等资源观测由消费方通过 Prombed 或自己的观测编排完成，不进入本包。

## 代码地图

| 文件 | 职责 |
|---|---|
| `model.ts` | CaseSet 选择 overlay、Outcome、Trial、Run 等纯数据名词 |
| `load.ts` | open/closed、Stage、Schedule 与 Pacing |
| `workload.ts` | 服务协议扩展契约 |
| `scheduler.ts` | 发压、熔断、请求上限与停止语义 |
| `reduce.ts` | Outcome 到 Window/RequestStats 的归约 |
| `engine.ts` | Experiment 与 Arm/Trial 编排 |
| `runio.ts` | Python schema 4 对齐的 raw/model 产物 |

## 关键约定

1. 请求按 dispatch 时刻归属 Window，不能按完成时刻移动慢请求。
2. `Workload.fire` 只记录事实，`judge` 是唯一 verdict 权威。
3. 未发出的请求与被强制中断的在途请求不进入延迟分布。
4. `trace_id` 等跨平面对齐键放在 `Outcome.meta`，落盘时不得丢失。
5. 通用包不写业务 SSE 语义；业务 Plugin/consumer 实现 Workload。
6. Case/CaseSet 归 spec-case；Perf Experiment 只通过 `caseMix` 选择 id 和设置 weight。
7. Kernel 对齐：Outcome 是 request Observation；request、window、run 是不同 Unit grain，应分别形成 Dataset / Worksheet。raw/model Run facts 可由不同 Workload judge、SLO 和 analysis 组件离线复评，实际组件配置由 EvaluationRun 记录，不得因换判定口径重新发压；详见 `../../../docs/kernel.md#dataset-与反复评估`。

## References

- `../../../spec/perf-contract.md` — 跨语言 canonical 契约
- `../../../spec/perf-run-schema.yaml` / `perf-outcome-schema.yaml` — 落盘 schema
- `../../python/perf_harness/AGENTS.md` — Python 实现代码地图
- `../../python/perf_harness/docs/load-model-redesign.md` — 加压模型
- `../../../spec/conventions.md` — 跨 harness 对齐键与产物布局
