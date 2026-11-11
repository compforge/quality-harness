# Dataset、复评与结果投影

固定观察事实后，应能更换分析组件而不再次触发被测系统。本主题定义事实版本、评估粒度和结果归属；
执行事实的生成与归约见 [Experiment 执行与产物](execution.md)。

## 数据对象

| 概念 | 语义 |
|---|---|
| **Case** | 可重用的结构化测试意图，具有稳定 `case_id`；canonical 资产由 spec-case 持有，可被多个 Harness 消费。 |
| **Observation** | 执行或采集后实际观察到的领域事实；必须保留来源 identity 与 provenance，不包含质量判断。 |
| **Unit** | Harness 声明的评估单元，也是 Worksheet 一行所代表的基本粒度；其数据来源是一个或多个 Observation 及适用的 Case / Annotation。Unit 拥有自己的稳定 key，唯一确定一行；Observation 尚未产生或失败时，该行可以保留 pending / failed 状态。 |
| **Annotation** | 运行当前评估前已经存在的人工、外部系统或模型监督信息，例如 label、reference 和复核结论；按 Unit 对齐并保留 producer 与 provenance。 |
| **Dataset** | 一组可复用、版本化的 Unit facts，固定 Case、Observation、Annotation 及其来源关系，不包含某次 EvaluationRun 的 Finding、Evaluation 或 Measurement。相同 Dataset 可以按不同侧重点反复评估。 |
| **EvaluationRun** | 对一个确定 Dataset version 执行一组 Detector、Evaluator、Measurer 与可选 Policy 的运行实例；它记录实际组件的 spec、模型、规则、版本和配置，并拥有 run identity、执行健康与对应 Worksheet。 |
| **Finding** | `detect` 从 Unit 中识别出的模式或异常，带有证据、严重度和原因假设，但不直接给出质量 verdict。 |
| **Evaluation** | Judge / Evaluator 根据契约对 Unit 作出的质量判断，包括状态、verdict、score 和 explanation；使用确定性规则还是 LLM 是实现方式，不改变其语义。 |
| **Measurement** | 从 Unit 直接提取的 token、耗时、调用量和资源用量等中性事实，不携带质量 verdict。 |
| **Worksheet** | 一个 EvaluationRun 的行式工作模型：以 Dataset Unit 为行，保存 seed 事实以及本次追加的 Finding、Evaluation、Measurement 和 cell state。它是生成报告和聚合指标的事实输入，不等于某一种物理文件格式。 |

## 数据流程

```text
Case / Experiment → execution → Observation
已有 trace / trajectory / recording → collection → Observation
  → Unit（Observation + 适用的 Case / Annotation）
  → versioned Dataset

Dataset + Detectors / Evaluators / Measurers / optional Policy
  → EvaluationRun
      detect   → Finding
      evaluate → Evaluation
      measure  → Measurement
  → Worksheet（Unit + Finding + Evaluation + Measurement）
  → Metric / Verdict / Report
```

### Dataset 与反复评估

所有 Harness 都按同一组顶层概念组织数据：执行 Case / Experiment 或直接采集已有证据，得到真实
Observation；结合适用的 Case 与 Annotation 建立评估 Unit，再将一组 Unit 固定为可复用 Dataset。
直接分析 trace、trajectory 时不要求补造 Case。每次运行选择的 Detector、确定性规则、LLM Judge、Measurer
与可选 Policy 直接定义评估侧重点；每次 EvaluationRun 形成自己的 Worksheet 和 Report。

Worksheet 的一行始终等于 Unit seed 加上本次 Finding、Evaluation 与 Measurement；聚合、透视和报告都是这张
表的下游操作。

Dataset 和 Worksheet 使用相同 Unit grain。一个 Harness 存在多种基本粒度时，应形成多个明确 grain
的 Dataset / Worksheet，不能把不同层级的行混进一张无从解释的大表。具体 grain 和 key 由领域文档
定义。

一个 Case 可以产生一个或多个 Observation；直接产出的 response、outcome 与经过采集、转换后形成的
trace、trajectory 在这一层没有本质差别。Observation 必须携带足够的来源 identity 和 provenance，
使其能够稳定对齐原始 Case、Run 和中间产物，不能依赖文件名或报告渲染阶段猜测关系。Case 提供输入、
预期、facets 和 ground truth 等稳定 seed，Observation 提供系统实际做了什么；Unit 在 Harness 声明的
grain 上将两者形成独立、可寻址的评估单元。

Dataset 固定 Unit facts 和已有 Annotation；EvaluationRun 只向 Worksheet 同行追加不同语义的列族：

- **Annotation**：人工、外部系统或模型预先提供的 label、reference 与复核信息；保留 producer 和
  provenance。数据由 LLM 生成并不会自动使它成为 Evaluation，语义取决于它是否在表达监督信息。
- **Finding**：Detector 在本次运行中发现的模式或异常；保存证据、严重度和可能原因，
  但本身不表示通过或失败。
- **Evaluation**：Judge / Evaluator 根据契约对 Unit 作出的质量判断和分数；即使内部调用
  LLM，它仍属于 Evaluation。
- **Measurement**：从 Unit 直接提取的 token、耗时、调用量和资源用量等中性事实。

`detect / evaluate / measure` 是并列的处理职责，对应产物 Finding、Evaluation 与 Measurement 是同一
Worksheet 上并列的结果列族；Policy 消费这些结果并生成
Verdict。Finding 不能绕过显式 Policy 自动变成门禁结论。

Case、Observation 或 Annotation 改变会形成新的 Dataset version；只更换 Detector、Evaluator、Measurer、模型、
规则、成本口径、Policy 或分析侧重点，应创建新的 EvaluationRun 并复用原 Dataset。EvaluationRun
必须记录实际运行的组件 spec 与配置，以支持复现和比较。Metric
是对已填充 Worksheet 按 run 或领域维度聚合后的结果，不是另一份与行数据脱节
的事实源。报告层只做 join、pivot、aggregate 和 render，不重新执行 Case、不重新读取远端 Source，
也不重新运行 Judge。内部 JSON 保存无损、可恢复的数据，`verdict.json` 是统一机器判定投影，HTML
是面向人的 UI 投影；需要 CSV 等格式时也从同一 Worksheet 派生。

所有 Harness 必须能映射到这套共同语义，但不要求共享同一个 `Dataset` / `Unit` / `Worksheet` 类或
schema。各领域拥有自己的 Unit grain、强类型 key、列类型、调度和聚合语义；物理实现也可以把
Dataset build、Observation production 和 Evaluation 融合在一个带 cell state 的 Worksheet/checkpoint
中，只要事实列与本次评估列可分离，并能复用 Observation 重新评估。

## 领域映射

- [Canonical Case](case-unification.md)：Case / variant、Outcome 与离线复判。
- [Trace](trace-harness.md)：Node、trace 和 corpus 的分析粒度。
- [Trajectory](trajectory-harness.md#3-评估)：Detector / Verifier 与 cost / effect 维度。
- [Perf](../spec/perf-contract.md)：请求和窗口事实、独立 Judge 与指标归约。
