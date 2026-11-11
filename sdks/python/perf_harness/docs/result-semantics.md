# Perf 结果语义

Perf 执行指定负载并保存观测，不要求事先定义验收阈值。SLO 配置、聚合验收和基于 SLO 的容量判定不属于 Perf Harness。

## 请求事实与分类

Runner 保存原始 Outcome：状态码、耗时、流式事件、错误和关联 ID。独立 Judge 返回
`RequestEvaluation(result, error_kind)`；result 为 `success`、`failure`、`rejected`、`timeout`。
缺少 Judge 结果是 `unjudged`，不是服务失败。HTTP 200 不自动证明 SSE 业务完成；业务 Judge
读取 Runner 保留的完成信号。默认 Judge 按传输与 HTTP 状态分类：429 为拒绝，408/504 或
Timeout 异常为超时，其余非 2xx 为失败；503 本身不能证明拒绝。项目可替换该规则。

## 时间窗口与统计

所有窗口采用 `[start_s, end_s)`，时间相对 ArmRun 发压起点。请求延迟按 dispatched_at
归入 cohort，即使请求在后续排空阶段完成。吞吐按 finished_at 统计实际完成事件。

| 指标 | 口径 |
| --- | --- |
| dispatch_rps | 窗口内实际发起数 / 窗口时长 |
| throughput_rps | 窗口内完成数 / 窗口时长 |
| success/failure/rejection/timeout/unjudged_rps | 对完成事件的五种互斥分类 / 窗口时长 |
| n / n_ok / n_judged | dispatch cohort 中完成数、成功数、已判定数 |
| error_rate | dispatch cohort 中已判定的非成功数 / n_judged |
| interrupted | 窗口内实际中断事件数；不属于完成吞吐 |
| n_interrupted | dispatch cohort 的中断数，用于关联该批请求的完整性 |
| n_dropped | 未发出的丢弃数，不属于服务响应 |

`completed = succeeded + failed + rejected + timed_out + unjudged`，五类速率之和等于
throughput_rps。超时只计入 timeout，拒绝只计入 rejection，不重复计为 failure。
没有已判定请求时 error_rate 的 MetricStore 读取为 Missing，报告显示缺失；原始计数仍保留。
中断和丢弃不进入延迟分布。nearest-rank 百分位索引为 `ceil(q*n)-1`。

## 执行与采集状态

Run.status 为 `completed`、`stopped`、`error`。正常结束只表示实验执行完成，不表示业务达标。
phase_errors 表示执行异常并停止后续 Arm；熔断或排空超时导致的请求中断表示 stopped。
请求级失败率本身不改变 Run.status；显式错误率熔断仍可提前停止当前 Arm，以保护运行。
CLI 退出码为 completed=0、stopped=2、error=1。

通用 verdict.json 仅保留执行诊断：执行异常映射 error，其余为 skipped（没有验收策略），
不输出业务 pass/fail。采集错误保存在 probe_errors 与 up=0，缺少指标不会补成零，也不自动改变执行状态。

## 离线数据

schema 7 保存运行状态、环境、请求时间线、独立判定与窗口统计。旧主 schema 不由新 reader 兼容。
load_run 不连接目标；更换 Judge 后可用 reduce_requests 重算每个 Window、Case 和 facet 的请求统计。
保存重算结果时保持 requests.jsonl 原样；report 只读取已保存的窗口结果，不隐式执行 Judge。
下游 API 数据按 Service / Operation 和实际窗口独立保存，不需要 Graph，也不能仅由相关性证明瓶颈。
