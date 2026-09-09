# trajectory_harness

## 项目定位与边界

trajectory_harness 评估 agent/workflow 的**决策与行动序列**：是否反复调用同一工具、绕路、
遗漏必要步骤，或在某一步做了错误决定。一条 Trajectory 可以是单个 agent loop，也可以是多个
loop 与确定性操作构成的 pipeline。它不替代 eval_harness 的最终回答质量评分，也不替代
trace_harness 对物理 span、耗时与错误传播的归因。

外部 session / trace 先由 `RecordingSource` 发现和读取，再经 `TrajectoryLoader` 转为业界
ATIF v1.7 `Trajectory`，再从中确定性派生 `Measurements`。Trajectory 与 Measurements 共同作为
Detector、Verifier 的输入；原生 ATIF 不需要再投影为一套 Harness 私有格式。轨迹事实、
评估结论、批量指标和报告逐层分离：Failure 不是低分，Measurement 也不是 Dataset 级 Metric。

## 代码地图与核心模块

```
trajectory_harness/
├── model.py             # 官方 ATIF 类型重导出 + namespaced extra 访问器
├── source.py            # RecordingQuery / RecordingRef / RecordingSource
├── dataset.py           # versioned Trajectory Unit facts / annotation relation
├── build.py             # Source + Loader → 固定 Dataset；领域对齐 annotation
├── failures.py          # LLM 等通用低基数 Failure taxonomy 与构造函数
├── loaders/             # 原生 ATIF 读取 + session/OTLP 等来源到 ATIF 的 Adapter
├── detect.py            # DetectorSpec / DetectionResult / Finding 与发现编排
├── verify.py            # VerifierSpec / VerificationResult 与判定编排
├── measure.py           # MeasurerSpec / MeasurementResult 与测量编排函数
├── metrics.py           # TrajectoryAnalysisRun / Metric 与 target/category 维度聚合
├── runner.py            # 固定 Dataset → Run（measurement → detection / verification）
├── runio.py             # dataset.json + 单次 run.json 持久化
├── report.py            # 纯 Run artifact → Report / HTML，历史外部加载
├── report_comparison.py # 调用方显式选 effect/cost 指标的 Pareto 投影
├── report_detection.py  # Detector catalog 与 Finding evidence 报告投影
├── report_provenance.py # generation provenance 分组与报告投影
├── verdict.py           # 运行健康 + 可选领域 Policy → verdict.json
├── pipeline.py          # Builder + Runner + Reporter 一键编排门面
├── detectors/           # 可复用行为模式发现；一个文件一种 Finding
├── verifiers/           # 硬规则与软规则 verifier；一个文件一种判定
├── measurers/           # 单轨迹事实测量；一个文件一种测量关注点
└── tests/               # ATIF、loader 与 verifier 契约测试
```

## 关键约定

- **ATIF 是唯一 Trajectory 契约**：直接依赖官方 `atif` v1.7 Pydantic models，不定义平行的
  `Trajectory / Step` schema。`dataset.json` 原样保存合法 ATIF；Harness 私有来源或诊断事实只能进入
  ATIF `extra.quality_harness`，不得新增私有顶层字段。
- **Source 只负责选择和读取**：Source 返回轻量 `RecordingRef` 和原始 `Recording`，不解析为
  `Trajectory`，也不执行判定；领域标签作为 `TrajectoryAnnotation` 与 Dataset 组合，不塞进 Source 契约。
- **Kernel 对齐**：Trajectory 是 Observation，`trajectory_id` 定义 Unit grain；`TrajectoryDataset` 固定 Case + Trajectory Unit 与 `TrajectoryAnnotation`。每次 `TrajectoryAnalysisRun` 记录实际选择的 Detector / Verifier / Measurer / Policy，并只填充自己的 Worksheet；同一 Dataset 可按成本或效果反复分析，详见 [`../../../docs/kernel.md`](../../../docs/kernel.md#dataset-与反复评估)。
- **两个正交维度**：Detector 与 Verifier 的 `category` 只表达关注面 `cost / effect`，`rule_type` 只表达规则形态 `hard / soft`；两者不可互相推导。`target` 另行表达一行当前分析谁，如 CCR 的 Review 1 / Review 2。
- **Loader 只做标准化适配**：`ATIFJsonLoader` 直接校验官方格式；session/OTLP Loader 把来源字段映射到
  ATIF 的 `agent / steps / tool_calls / observation / metrics`。无法无损映射的来源事实保留在 namespaced
  `extra`，Detector / Verifier 不读取原始 Recording。Dataset Builder 也通过 `extra.quality_harness.recording`
  附加 `recording_id` 与 URI，不改变 ATIF schema。
- **执行层级遵循 ATIF**：顺序由 ATIF `step_id` 表达，多 agent 使用 `subagent_trajectories` 与
  `SubagentTrajectoryRef`。OTel span parent、operation、duration 等遥测事实仅作为来源扩展保留，
  不提升为另一套通用 Step 字段，通用模型不引入 Stage。
- **Failure 是可选执行事实扩展**：ATIF 尚未定义统一失败 taxonomy 时，Loader 可在
  `extra.quality_harness` 中保留 `Failure / ExecutionResult`；分类使用 `kind / phase / error_type` 三个
  正交维度，不改变 ATIF 顶层与 Step 契约。
- **Detector Finding 不回填 Failure**：`DetectionResult` 是 Detector 的执行结果，诊断发现写入
  `findings`，并用 `hypotheses` 表达可能原因；业务契约不合格由 Verifier 使用 `verdict=fail`，
  各组件的 `status=error` 只表示自身异常。
- **职责由输出语义区分**：Detector 做数据挖掘和发现，只输出 Finding；Verifier 对显式判据做验证，
  输出可选 `verdict` 和/或 `score`。两者都可度量成本或效果，也都可使用硬规则或软规则。Measurer
  只是派生 Measurement 的实现机制；无法适用和插件自身报错通过各自 `status` 表达，不能伪装为
  0 分或 0 成本。
- **Finding 是诊断发现**：Finding 表示 Detector 解释出的具体问题模式，用 `step_ids` 锚定证据并用
  `hypotheses` 记录可能原因；可直接计数、求和的 token、调用次数和耗时仍是 Measurement。
- **通用 Detector 沉淀 Finding**：围绕 system prompt、tool set 和 loop mechanism 积累可复用
  的行为发现，但不把发现直接归因到某一组成；业务契约和跨 loop 编排由 domain Verifier 负责。
- **Harness 与 trajectory skill 分工**：ATIF 拥有轨迹格式；本包沉淀 Measurements、通用规则和
  执行流程，不承担业务归因与改造。trajectory skill 驱动消费项目取数、结合业务上下文判断问题
  源于个体使用、agent、集成/基础设施还是知识/组织缺口，并推动项目实现和注册业务专属的
  Measurer / Detector / Verifier 子类，完成最后一跳。
- **Metric 只表示批量聚合**：单轨迹的派生值叫 Measurement；Metric 从某个 AnalysisRun 的
  Worksheet 聚合，并携带 Dataset version、组件 spec、run、target、category 等比较维度。
- **HTML 由 trajectory_harness 对外提供**：本包决定 Dataset、Failure、Verifier、Metric
  与趋势章节，再投影到中立的 `harness_common.report_kit`；effect-cost Pareto 必须由业务显式选择
  两条 Metric 轴，Harness 不猜 effect。业务消费者不自行拼 HTML。
- **三段式生命周期**：`TrajectoryDatasetBuilder` 从 Source/Loader 构建带 annotation 的
  版本化 Dataset；`TrajectoryAnalysisRunner` 以实际选择的 Detector / Verifier / Measurer / Policy 消费固定 Dataset；`TrajectoryReportBuilder`
  只消费已持久化 Run artifact。`TrajectoryHarness` 只是编排门面，不吸收领域逻辑。
- **Dataset 先于 Run，Run 先于视图**：`dataset.json` 保存可反复评估的 Unit facts 与 annotation，
  `run.json` 只保存本次评价并引用 Dataset 中的 trajectory id，`report.html` 是纯下游视图。
  历史 run 由 Reporter 外部加载，不复制进新 `run.json`；重渲染不能重新抓 Source 或运行插件。
- **Finding 不自动决定 Verdict**：没有领域 `TrajectoryVerdictPolicy` 时 run 为 `skipped`
  但仍产出分析产物；Dataset 构建或 Detector/Verifier/Measurer 插件异常为 `error`。
- 不默认合成跨 Verifier 综合分；需要门禁或总分时由业务显式定义 Policy。

## References

- 设计与外部实现取舍：[`../../../docs/trajectory-harness.md`](../../../docs/trajectory-harness.md)
- ATIF v1.7：<https://github.com/harbor-framework/harbor/blob/main/rfcs/0001-trajectory-format.md>
- OTel GenAI 语义约定：<https://opentelemetry.io/docs/specs/semconv/gen-ai/>
