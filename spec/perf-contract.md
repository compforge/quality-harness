# Perf Harness 跨语言契约

Python 与 TypeScript 共享本契约；各语言保持惯用 API，功能覆盖可以不同。当前产物使用 schema 6。

## 事实骨架

`ExperimentRun.executions → ArmRun (Execution) → OperationRun → Outcome`。
Arm 是 ResourceProfile × LoadPlan 的具名配置；一次 ArmRun 固定目标 Service/Workload、Arm 与 Case 选择，
组织加压器产生的 N 次调用。Run 不直接管理全部调用。common 拥有骨架、身份与 Workload 语义；
perf 的协议适配器称 Runner，避免把它与运行平台承载单元 Workload 混用。

Case/CaseSet 归 spec-case；实验只选择 Case 并设置本次使用的权重，不修改资产。
简单 Case 一次触发对应一个 OperationRun；这不是对所有领域多步骤 Case 的限制。
`arm.id` 在 Experiment 内稳定；`ArmRun.id` 带 run identity；`OperationRun.id` 在 ArmRun 内唯一。
一个 ExperimentRun 内每个 Arm 只执行一次；重复 Arm 在 setup 前拒绝。配置 hash 只能消除显示标签冲突，
不能充当重复执行序号。Reader 拒绝重复 ArmRun ID，避免重载时静默合并调用与判定。
RequestRecord 以 operation_run_id 引用实际调用；drop 没有 OperationRun。Outcome 仅存一份。
Service 持有 common Component/Repository/Forge/Environment/Workload 身份；落盘只保存非敏感身份，
不持久化访问凭据、HTTP headers、kubeconfig 内容或执行期客户端。

## 调度契约

LoadPlan 必填 `request_rate` 与 `max_inflight`，`hold_s` 默认 60 秒。
`warmup.step_s` 默认 5 秒：从 min(1, request_rate) 开始逐档倍增，达到目标速率或首次达到
max_inflight 后进入 hold。step_s=0 或 request_rate=inf 直接进入 hold。

hold 从实际进入时计时，维持配置的目标 request_rate 作为补位节奏；预热触顶不冻结低速档。
满在途暂停源头，空位出现后继续发起，不排队、不虚构 drop、不突发补发。有限速率支持
constant/poisson 间隔，seed 保证本语言内重复性；inf 按空位补充。
并发覆盖完整请求生命周期，包括 SSE 完整读取。调度器卡顿不在恢复后补发错过的请求。

Stage 显式声明 duration_s/request_rate/max_inflight/kind，支持 warmup/hold/ramp；简单配置
生成 warmup 与 hold 计划。显式 stages 自行决定时长，不叠加默认 warmup 和 hold。
降并发不取消已有请求；单个 LoadPlan 不混合有限速率与 inf。

hold 到期停止发压。cooldown 最多等待 cooldown_timeout_s（默认 180 秒），然后取消并 join
剩余请求，再释放客户端。活跃任务数有界，历史证据内存随实际请求数增长。
Runner 必须支持取消，不能自己创建加压循环。

## Runner、Judge 与生命周期

Runner.operation 标识服务能力，fire 执行一次调用并返回原始 Outcome；独立纯 Judge 返回
RequestEvaluation，按 OperationRun ID 保存，不回写 Outcome。默认 Judge 只判断传输/HTTP 状态；
SSE 业务完成规则由 consumer 显式声明。Outcome.meta 可记录 trace_id、message_id、完成事件等原始信号。
first_byte_ms 只表示首字节，不能自动命名 TTFT；业务 token 时刻由 Runner 识别。

生命周期：setup → warmup → hold → cooldown → cleanup。
排空和停用是内部动作；可选 cooldown_s 在停用后延长资源观测。
Python 资源观察覆盖发压、排空、停用与 cooldown；TypeScript 资源观察仍由消费方负责。
普通生命周期异常记录 phase_errors，终止 sweep，verdict=error；控制流取消允许继续向调用方传播。
停止发压时立即记录 measurement 边界、停止原因与在途数量；取消并 join 后补齐中断清点。
这些事实随 ArmRun 生命周期维护，不依赖 scheduler 正常返回。Judge 异常保留已完成 Outcome，
标记缺失判定并使 Run 失败；排空期间的异常不得重置或延长 measurement，也不得改写已发生的停止原因。
每个实际中断请求保留独立事实，不执行 Judge，不把短暂取消耗时混入延迟样本。

## 时间与统计

所有时刻是相对 ArmRun 发压起点的秒数；Window 使用半开区间 `[start_s,end_s)`。

- arrived / arrival_rps 按源头实际接受的发起机会 scheduled_at 归窗；arrived_at 保留实际处理时刻。
- dispatched / dispatch_rps 按实际 dispatched_at 归窗。
- completed / throughput_rps / succeeded / success_rps 按实际 finished_at 归窗，只含 finished 请求。
- n / n_ok / error_rate / latency 按 dispatch cohort 归窗，包含排空后才完成的 Outcome。
- inflight_peak/end 由实际调用起止事件求得，包括前一个窗口带入的请求。
- scheduler_lag_ms = (arrived_at - scheduled_at) × 1000。

measurement 从首个实际 hold 开始；warmup/ramp/hold 对齐实际执行的 Stage；
cooldown 记录停压后的完成事件与资源观测。Window.end_reason 记录阶段结束原因，
limited_s 记录已经允许发起但被在途上限阻塞的时间。nearest-rank 百分位索引为 `ceil(q*n)-1`。
丢弃、中断不进入延迟分布；无完成样本的延迟/错误率 SLO 为缺数据。
co_biased/high_drop/incomplete/few_samples 等可信度标记必须随 summary 保存。

Python capacity 只读取有请求、无丢弃/中断/未判定且匹配 SLO 全通过的完整 hold。
有限速率窗口被在途上限阻塞时，不确认配置速率容量，也不参与资源曲线外推。
无 SLO 的正常运行 verdict=skipped，不表示容量通过。TypeScript 当前不提供 SLO 评估与 HTML 报告。
离线可更换 Judge、重算统计与 SLO；重渲染报告不重新发压或判定。当前 perf 请求判定表是领域投影，
未实现通用 EvaluationRun/Worksheet 的全量持久化，不把设计目标写成已实现能力。

## 比较条件

有限速率的默认扫描轴为 request_rate，无限速率的扫描轴为 max_inflight。另一轴的完整调度、
资源配置、到达分布、seed、时长、warmup、drain 与熔断条件必须相同；扫描轴的阶段形态按峰值
归一化后也必须相同。不同条件分组，条件相同且至少有两个不同档位才绘制响应曲线或拟合斜率。
固定有限速率、只改变并发上限的实验保留为不同条件的结果点，不推导速率容量曲线。

分析、SLO 容量汇总与报告共用比较分组；图表标明扫描轴及单位，汇总表保留两轴峰值，
精确阶段配置保留在 Arm 中。分组标签中的短 hash 仅供显示，完整配置决定分组归属。

## 产物

| 文件 | 契约 |
|---|---|
| run.json | schema=6，executions 数组；无重复的 arm_runs/outcomes 存储 |
| requests.jsonl | 每行 arm_run_id + request + 可选 operation_run（拥有唯一原始 Outcome） |
| evaluations.json | ArmRun ID → OperationRun ID → RequestEvaluation |
| timeseries.csv | arm_run,series,t,value 的资源采样 |
| verdict.json | 统一 verdict-schema.yaml；执行异常 error，提前停止/中断 fail |

JSON 中无限速率写为字符串 `inf`；有限速率为 number。可空请求时刻允许缺省或 null。
Reader 必须恢复原始关联与完整请求记录；支持当前 schema 的可选扩展字段，不接受旧主 schema。
跨语言共同 fixture 位于 [conformance/perf](../conformance/perf/README.md)。字段定义见
[Run schema](perf-run-schema.yaml)、[Request schema](perf-request-schema.yaml) 与
[Evaluation schema](perf-evaluation-schema.yaml)。
