# eval_harness（table-centric：非确定性评测）

## 项目定位与边界

接口产出是非确定的（自然语言回答 / 工具调用序列），关心回答质量、是否拒答、引用是否准确。**实验优先**：一次评测是一个 Experiment，可在多个对照臂（Arm）上跑同一评测集，做跨模型/跨参数对比。

### 脊柱

```
canonical CaseSet → EvalSet runtime ──(solver / scorer)──▶ Worksheet ──pivot──▶ report
```

一个 evalset，过一个或多个 Arm，产出一张内存 Worksheet，reconciler 逐 cell 填，report 纯 pivot 渲染。**不是 prepare→run→eval 的串行流水线**。

## 代码地图与核心模块

```
eval_harness/
├── model/       # experiment(Experiment/Arm/Business/LLMSpec) · evalset(EvalSet/eval_view/FacetSchema；case=common.Case) · sample(MetricResult)
├── worksheet/   # worksheet(Worksheet/Row/cells) · checkpoint(jsonl + 异步 Checkpointer)
├── metric/      # base(BaseMetric+WEIGHT) · aggregate · builtins · registry
├── schedule/    # ratelimit(per-endpoint AIMD) · reconcile(ReconcileEngine)
├── report/      # pivot(arm_id/facet/compare) · render(md/csv)
├── produce/     # mock(echo)；真实 producer 落在消费方仓库
└── config.py · engine.py(run_experiment) · cli.py · materials/
```

## 关键约定

- **Experiment** = common 的具名验证意图 + `service`（Component + Environment + 基线 SUT 配置）+ `arms` + CaseSet 引用 + `metrics` + `weights`
- **CaseSet 归 spec-case**：Eval 完整加载其 identity / sources / facets / cases，只解释
  `input` 与 `judge.eval`；Experiment 不得覆盖资产字段
- **Kernel 对齐**：Solver 填入的 response / retrieval / citation 是 Observation，`arm_id × corpus × case_id` 是 Unit grain；已完成 solve cell 的 Unit facts 构成可复评 Dataset，本次选择的 scorer / metric / weight / judge model 直接定义评估侧重点。当前 Worksheet 同时 checkpoint Observation production 与评分结果，但 score cell 必须可在复用 solve cell 的前提下重新填充；详见 [`../../../docs/kernel.md`](../../../docs/kernel.md#dataset-与反复评估)。
- **Arm**（对照配置）= `service ⊕ overrides`，分 **heavy**（provisioned resource+sources，有 prepare/clean 和复用 key）+ **light**（model/params，按调用施加）；`Arm.key` 只 hash heavy 字段 → 只换模型的 Arm 共享同一份 provision
- **Trial** = 某个 Arm 的一次真实执行，Worksheet 以显式 `arm_id` 记录并对齐其结果
- **Worksheet**（大表）= common `ExperimentRun` 的 Eval 子类，也是一次 EvaluationRun 对 Dataset 的 rows = (arm_id × case) 填表结果；每 cell 带 PENDING/OK/FAILED，引擎唯一真相 + checkpoint
- **reconciler**（缺啥补啥）= 扫描非 OK 且依赖就绪（provision→solve→score）的 cell 派工填；per-endpoint rate-gate；resume = reload 后再扫一遍
- **MetricResult 双通道**：quality（0~1 → 加权 overall）vs measurement（value+unit，p50/p95，不进 overall）；`score=None` = 弃权（不当 0）
- **产物两形态**：`worksheet.jsonl`（无损 checkpoint，可断点续跑）vs `results.csv`（有损扁平投影，人工 review 用）
- 真实 Provisioner/Solver 是消费方关切，落在消费方仓库；本包用 `--mock` 自跑

## 开发与测试

从 `sdks/python` 运行无需外部服务的 smoke 评测：

```bash
uv run python -m eval_harness.cli eval_harness/materials/experiments/smoke.yaml --mock --fresh --runs-dir /tmp/eh
```

共享 CaseSet 的跨领域行为由仓库根目录 `conformance/case/` fixture 验证。
