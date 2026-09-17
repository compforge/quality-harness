# Perf conformance

公共 fixture 表达 schema 5 的一次完成调用和一次未发出的 drop，供 Python/TypeScript 共同读取。
`basic.run.json` 只有 Execution/Arm/Window 投影；`basic.requests.jsonl` 保存调度记录与唯一原始
OperationRun/Outcome；`basic.evaluations.json` 单独保存判定。

实现必须保留 arm_run_id / operation_run_id / case_id 与 trace_id，恢复 inf 速率，接受可空时刻，
并能从同一事实重算 dispatch cohort 延迟与实际事件吞吐。未发出请求不能生成虚构 Outcome。

契约见 [perf-contract](../../spec/perf-contract.md)，schema 见
[Run](../../spec/perf-run-schema.yaml)、[Request](../../spec/perf-request-schema.yaml)、
[Evaluation](../../spec/perf-evaluation-schema.yaml)。

`judge-failure.json` 是 Python/TypeScript 共同执行的生命周期场景：Judge 在 measurement 或 drain
期间失败时，已完成请求、measurement 边界、停止原因、在途数量、中断清点和重载结果必须一致。
取消处理刻意延迟，以验证清理耗时不会进入 measurement。
