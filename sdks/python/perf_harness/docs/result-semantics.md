# Perf 结果语义

Perf 执行指定负载并保存观测，不要求事先定义验收阈值。SLO 配置、聚合验收和基于 SLO 的容量判定不属于 Perf Harness。

## 请求事实与分类

Runner 保存原始 Outcome：状态码、耗时、流式事件、错误和关联 ID。独立 Judge 返回
`RequestEvaluation(code="success")`；code 是非空字符串，只有 `success` 表示成功，其余值都表示失败。
code 由 Judge 定义，使用稳定名称（如 `model_unavailable`），不把动态错误文本或请求 ID 当作 code。
原始 HTTP status、异常细节仍在 Outcome 中；缺少 Judge 结果单独计为 unjudged，不占用一个 code。
HTTP 200 不自动证明 SSE 业务完成；业务 Judge 读取 Runner 保留的完成信号。
默认 Judge 将 2xx 映射为 success，其余 HTTP 状态映射为 `http_<status>`；Timeout 异常为 timeout，
其它异常为 transport_error，没有状态或异常信息为 unknown。项目可替换这些映射。

## 时间窗口与统计

所有窗口采用 `[start_s, end_s)`，时间相对 ArmRun 发压起点。请求延迟按 dispatched_at
归入 cohort，即使请求在后续排空阶段完成。吞吐按 finished_at 统计实际完成事件。

| 指标 | 口径 |
| --- | --- |
| dispatch_rps | 窗口内实际发起数 / 窗口时长 |
| throughput_rps | 窗口内完成数 / 窗口时长 |
| completed_by_code / throughput_by_code | 按 Judge code 分组的完成数 / 完成速率 |
| succeeded / success_rps | 从 code=success 的完成数和速率派生 |
| unjudged / unjudged_rps | 没有 Judge 结果的完成数和速率，不属于任何 code |
| n / n_ok / n_judged | dispatch cohort 中完成数、成功数、已判定数 |
| error_rate | dispatch cohort 中已判定的非成功数 / n_judged |
| interrupted | 窗口内实际中断事件数；不属于完成吞吐 |
| n_interrupted | dispatch cohort 的中断数，用于关联该批请求的完整性 |
| n_dropped | 未发出的丢弃数，不属于服务响应 |

`completed = sum(completed_by_code.values()) + unjudged`；各 code 速率之和加 unjudged_rps 等于
throughput_rps。失败吞吐可由所有非 success 的 code 速率相加得到，不预设拒绝、超时等固定字段。
error_breakdown 按 dispatch cohort 内的非 success code 统计，不能与完成事件的 by-code 数量混用。
即使业务使用名为 `unjudged` 的 code，它也表示明确的失败判定，与没有 Judge 结果独立。
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


## 按 code 查询与展示

`request.completed_by_code{code="model_unavailable"}.value` 与
`request.throughput_by_code{code="model_unavailable"}.value` 通过 MetricStore 读取指定窗口的数量和速率。
可再加一个已声明 facet label 选择请求分组。code 是开放的结果维度，不要求预注册；没有观测到的
code bucket 读取为 Missing。默认 Markdown、HTML 与 CSV 包含完整 code 映射，原始键值落在 run.json。
