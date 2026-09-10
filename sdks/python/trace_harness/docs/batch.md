# Batch 分析

批量分析回答一批运行记录的共同模式、分布与差异；耗时只是一个应用场景。
共享模型与业务扩展见 [kernel](kernel.md)，读取与资源控制见 [load](load.md)。

## 选择与执行

```text
Source → select → Dataset → detect → Findings / Measurements → 聚合、对比、下钻
```

select 接收时间、属性、错误条件与明确 trace IDs，固定实际成员并记录查询和选择上限。
`SpanQuery.order="latest"` 按最新匹配 span 的开始时间倒序选择不同 trace，
相同时间按 trace ID 排序；`operation_names` 可限定请求入口。时间与属性过滤先于排序及 trace 数量上限。
业务负责定义何种 span 代表请求开始，不把任意内部调用的新近活动当作新请求。
命中 span 用于决定 trace 是否入选，不能作为完整 tree 的替代。Dataset 与分析规则分开，
同一 Dataset 可用于不同问题。

```python
async with harness.open(source, work_dir="trace-work") as session:
    dataset = await session.select(query)
    result = await session.detect(
        dataset, detectors=["http_request_patterns"], metrics=["self_ms"]
    )
```

node detector 逐 trace 后序执行，batch detector 每 Dataset 执行一次：

```python
async def inspect_dataset(dataset, context):
    async for analysis in context.trees(dataset):
        for node in analysis.trace.nodes:
            value = await analysis.fact(node, "business_fact")
            # 汇总必要值，或通过 context.emit(finding) 增量输出。
    return []
```

业务贡献通过 `TraceContributions.batch_detectors` 注册。规则可以重新访问指定 tree 或读取
本 Run 的结果行，不需要直接处理远端存储。不要把迭代出来的全部 analysis 收集到内存。

## Detector 组合与依赖

同一个问题可以由多个独立 detector 分别观察，再由汇总 detector 消费其结果。例如，请求入口到
意图识别结束的分析保留总 detector 和各阶段 detector，汇总 detector 对齐同一请求的阶段耗时、
共同问题和缺失证据。组合仍是普通 detector 声明依赖后的行为，不引入独立的 CompositeDetector
类型，也不要求业务函数直接调用其他 detector。

### 跨语言定义

Node 与 Dataset 共用的定义见 [kernel](kernel.md#detector-定义与依赖)。
Detector 用定义对象表达稳定身份、依赖和执行逻辑。`id` 是显式业务标识，不依赖 Python
`__name__`、TS 函数名或注册顺序；`requires` 引用本作用域中其他 batch detector 的 id；
`detect(dataset, context)` 保持普通函数。没有依赖的 detector 使用空 `requires`。

Python 已实现定义对象与依赖执行；下面以 TS 写法说明跨语言契约，TS 尚未实现该能力：

```typescript
const intentionStages: BatchDetector = {
  id: "intention_stages_analysis",
  requires: [
    "intention_latency_analysis",
    "request_preparation_analysis",
    "query_rewrite_analysis",
    "parallel_preparation_analysis",
    "preparation_routing_analysis",
    "intention_recognition_analysis",
  ],
  detect: summarizeIntentionStages,
};

async function summarizeIntentionStages(dataset, context) {
  const total = context.result("intention_latency_analysis");
  const parallel = context.result("parallel_preparation_analysis");
  // 检查执行状态与业务 coverage，读取结构化 findings，按请求关联。
  return summarize(total, parallel); // 示意：实际汇总读取全部阶段。
}
```

Python 从 `trace_harness` 导入 `Detector`，使用
`Detector(id="...", requires=(...), detect=summarize_intention_stages)`；`BatchDetector` 是其
Dataset 输入类型别名。
`context.result(id)` 返回共用的 `DetectorResult`，通过 `status` / `error` 检查执行，
`findings()` 逐行读取结构化输出。持久化路径由 Run 的执行记录提供，不属于公共结果接口。
定义对象在 `TraceContributions` 中显式注册。依赖是静态数据，框架能够在读取 trace 前验证并规划；
decorator 可以是语言层语法糖，但不成为公共契约。JSON/YAML 配置和运行时动态调用依赖不属于
此方案的必要部分；这里也不改变逐 Node detector 的执行粒度。

### 执行与结果访问

业务声明依赖、结果含义和关联规则，harness 拥有依赖解析、调度、执行去重与持久化：

1. 从用户选择的 detector 展开传递依赖，检查重复 id、未知依赖和循环依赖，再执行分析。
   Run 记录用户选择和实际展开的执行集合，单独选择汇总 detector 即可运行完整依赖链。
2. 按依赖顺序执行，所有直接依赖结束后才调用消费方；无依赖关系的规则不依赖注册顺序。
   同一 Run 中同一 detector 的相同配置最多执行一次，共享依赖和显式重复选择复用该次结果。
   同一 id 不允许绑定不同配置；需要比较不同配置时使用独立 Run。
3. `context.result(id)` 只读取当前 detector 已声明的直接依赖结果，不触发执行。返回值提供
   执行状态、错误信息和可迭代的结构化 findings；大量结果从落盘记录读取，不要求全部常驻内存。
   未声明的依赖访问报错，避免重新产生隐含的执行顺序。
4. 执行器为结果记录生产 detector id 与 Run 身份。Finding 的规则来源可以更细，不承担执行身份；
   汇总方不扫描全量 findings 猜测生产者，也不解析自然语言 note。原有 Measurement 和 Finding
   继续承担度量和观察，结果访问对象只提供该次执行的状态与输出视图。
5. 空 findings 且成功、执行失败，以及成功执行但业务 coverage 不完整必须可区分。依赖失败
   不终止无关 detector；汇总方仍能读取失败状态，按业务规则给出明确的不完整观察，不能将失败
   当成正常空结果。失败 detector 的部分输出如被保留，也必须带失败状态，不作为完整结果消费。
6. 结果复用限制在当前 Run；重新分析同一 Dataset 创建独立结果。Source 骨架和字段缓存仍可
   跨 Run 复用，不因执行结果去重而改变数据加载策略。

Detector 依赖与证据依赖是两个层次：`requires` 决定分析之间的先后关系；Fact / Measurement
及其字段依赖继续通过已有运行时准备。框架不根据 detector 名称推断业务指标，消费方可在普通
阶段 detector 内请求度量。汇总 detector 通常只读结构化结果，不再获取 tree 或原始 payload。

### 阶段耗时汇总的业务约束

AS 可以将总耗时与五段耗时关联成逐请求记录，再统计阶段占比、慢请求分布与共同告警。
这些语义留在业务 detector，通用 harness 不认识意图识别或 planit：

- 对齐 trace、request 和 prepare 执行实例，明确一对多关系；direct 与 queued 起点分别统计。
- 完整路径按单个请求核对阶段之和与总耗时，缺失阶段保留不适用原因，不补零。
- 先计算逐请求占比再聚合，不将阶段中位数相加；并行分支与父子调用按已有区间度量处理。
- 总 detector 和阶段 detector 引用相同告警时，按原始证据身份关联，避免重复计数。
- 综合结论保留依赖结果及 trace/node/span 引用，报告可下钻到支撑结论的阶段与调用。

共享验收 fixture `conformance/trace/detector-dependencies.json` 与 Python 运行测试覆盖：只选择汇总入口、共享依赖只执行一次、注册顺序无关、未知依赖与环检测、
依赖失败/空输出/部分 coverage 区分、汇总证据溯源，以及缓存完整时无额外 Source 读取。
Python 与 TS 对齐这些行为和 fixture；接口示例不表示 TS 已交付此能力。

## 结果与统计口径

逐 trace 保存标量 facts、Measurements、完整 Findings 和失败记录，保留 trace/node/span 溯源。
默认报告从磁盘数值表计算分组均值、p50/p95/p99，不平均各小批次分位数。
同时报告 selected/succeeded/failed，读取失败不能计作正常的空 trace。

普通 HTTP 慢请求与连续串行同 API 的规则在 batch 中保留完整召回；单条展示可限制呈现条数。
client/server 配对后计作一次请求，duration sum 不等于 wall-clock，父子重叠区间不能简单相加。
streaming、模型调用与普通 HTTP 的适用范围由协议证据和业务规则确定。

局部规则按 trace 流式执行；全局规则先持久化所需值，再统计或二次遍历。现有 corpus 的表算子
仍可用于显式物化的小规模分析；部分 legacy gates 会显式读取表，不代表所有 batch 路径都具有
相同内存边界。

## 对比、报告与下钻

`BatchResult.compare(baseline)` 比较相同 kind/name/metric 的统计值，包含样本数量与 p50/p95
变化。比较的前提是查询范围、业务版本、流量组成与指标定义可比；分布变化本身不能证明因果。

例如 memory KB client 优化，可选择改动前后相近流量，比较 HTTP 耗时与规则命中，并查看异常
样本的调用栈。客户端复用收益不能仅由总耗时降低推断，还需要控制下游负载和请求构成。

批量 Finding 的 note、结构化结果行与证据引用由通用报告展示；规则只贡献数据，不自行渲染 HTML。
批量默认输出汇总 HTML，不为每个 trace 生成 HTML。发现异常后用相同 Dataset 的 trace ID 下钻，
复用证据缓存。Finding 是分析观察；只有显式 gate 才产生 pass/fail verdict。

CLI `trace batch experiment.yaml` 支持 source、select、load、detectors、metrics、diff 与 gates。
trace-as 的 batch 入口负责环境、凭据和 AS 查询条件，核心执行留在 SDK。
trajectory 的批量分析可以沿用选择与按需访问思想，但其业务模型与规则仍归 trajectory_harness。

Run 的 `manifest.json` 保存实际展开的 detector 和依赖，`detector_runs.jsonl` 保存每个执行的
状态及输出路径，`findings.jsonl` 中的 `detector_id` 标识生产者。执行失败保留报告并将 Run 标记
为 partial，`summary.detector_failures` 供宿主 CLI 判断退出状态。业务 coverage 仍由 findings 表达。
