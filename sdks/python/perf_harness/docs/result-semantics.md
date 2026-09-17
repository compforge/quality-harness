# Perf 结果语义

## 事实与判定

Runner 只返回原始 Outcome；独立 Judge 返回 RequestEvaluation，按 OperationRun ID 保存。
HTTP/SSE 状态、完成事件与 trace_id 留在 Outcome；ok/error_kind 不写回事实。
未发出的 drop 只有 RequestRecord。已经发出但被取消的调用保留 OperationRun 与 interrupted 标记，
不执行 Judge、不进入延迟样本。请求异常保留真实经过时长；生命周期异常另记 phase_errors。

## Window

所有边界使用相对 ArmRun 发压起点的秒数与半开区间 `[start_s, end_s)`：

| 指标 | 归属口径 |
|---|---|
| arrived / arrival_rps / dropped | 计划到达时刻；包括截止后登记的 scheduler_deadline |
| dispatched / dispatch_rps | 实际调用开始时刻 |
| completed / throughput_rps | 实际完成时刻，含成功与失败 |
| succeeded / success_rps | 实际完成时刻，且 Judge 通过 |
| n / n_ok / error_rate / 延迟 | 本窗 dispatch 的请求 cohort；排空后完成的请求仍归本窗 |
| inflight_peak / inflight_end | 真实调用起止事件，包含上个窗口尚未完成的请求 |
| scheduler_lag_ms | arrived_at - scheduled_at；量化加压器处理到达的延迟 |

measurement 排除 warmup；Stage 形成 ramp/hold 窗口；drain 单独展示停止发压后的完成事件；
cooldown 是 deactivate 之后的资源观察。不能用 cohort 样本数除以窗口时长冒充完成吞吐。
nearest-rank 百分位使用 `ceil(q*n)-1`；无完成样本时，延迟/错误率 SLO 返回 Missing 而不是零值通过。

## 判定与可信度

错误率 breaker 控制本 ArmRun 的停止；`abort_on_fail` 控制 Arm 之间是否继续。
phase_errors 使 verdict=error；提前停止或 interrupted 使 run 失败；否则按显式 SLO 汇总。
无 SLO 的正常执行只证明完成了测试，verdict=skipped，不表示容量达标。
缺数据的 SLO 为 skipped；`strict_slo` 决定是否阻断，cooldown 缺数据默认阻断。

capacity 只读取完整 hold 窗口：有已完成请求、无丢弃/中断/缺失判定，且该窗口匹配的 SLO 全部通过。
其含义是“本轮该配置已观测通过”，不能由短时实验外推长期稳定容量。
inf 的响应相关补充会产生 coordinated omission；有限速率达到并发上限时也会遗漏慢请求群体。
`co_biased`、`high_drop`、`incomplete` 和 `few_samples` 随 summary 保存，不只出现在报告文案中。

离线重算应保留 requests.jsonl 原样，只更新 evaluations 与相应 Window/SLO 投影；报告是这些投影的
下游，不再接触被测服务。当前 perf 使用自己的请求判定表，不宣称已实现通用 EvaluationRun/Worksheet 存储。
