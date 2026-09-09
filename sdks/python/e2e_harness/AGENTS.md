# e2e_harness（确定性契约测试：判定即数据）

## 项目定位与边界

接口结果是确定的：合理输入 → 固定响应，错误输入 → 固定错误码。不关心"回答质量"——那是 `eval_harness` 的事；也不关心压力下的表现——那是 `perf_harness` 的事。

**命脉（判定即数据）**：case 是 spec-case 持有的结构化数据——`input` + `judge.e2e.assert`（`{path,op,value}`）。`engine` 吃 canonical Case → 协议 runner 发请求 → 跑 assert → `verdict`。两个编写前端归一到同一 Case：手写 CaseSet，或 `@case/@spec` 的 NL marker 经 `casegen` 编译。

## 代码地图与核心模块

目录按 pipeline 阶段切，链路 `NL marker →(casegen) canonical Case →(engine) verdict` 在结构上可读：

```
e2e_harness/
# ── ① NL authoring 前端：NL marker → canonical Case ──
├── casegen/
│   ├── contract.py # @case/@spec marker + CaseSpec/Case + case_hash（marker 形状）
│   ├── discover.py # AST 扫 @case/@spec(+ *_cases.yaml) → DiscoveredCase（不 import 被扫源码）
│   └── compiler.py # Compiler seam(Stub/Draft/未来 LLMCompiler) + compile/check/list + casegen CLI
# ── ② 执行 + 判定即数据：canonical Case → verdict ──
├── engine.py    # run_case/run_cases：Case → runner → assert 求值 → Verdict
├── caserun.py   # prepare/execute/judge/cleanup 生命周期、独立阶段 budget 与结果证据
├── matrix.py    # variant Cartesian product；variant 进入 arm_id + facets
├── temporal.py  # phase deadline 感知的 poll/retry/consistently
├── assertion.py # judge.e2e.assert 求值器：{path,op,value} over response view（结构化、纯函数）
├── cli.py       # `e2e run case.yaml --base-url … → verdict.json`（+ __main__；JSON/SSE）
# ── ③ 复用层 ──
├── runner/      # 协议适配器（sync+async × JSON/SSE）→ 统一 Outcome（engine 复用）
├── judge/metric/# 软评分 BaseMetric[Outcome]
└── core/        # common.Experiment 的 E2E 子类 + E2EConfig / load_config + profile/capability gating
```

> 输入 Case/CaseSet 归 spec-case；输出 verdict 与 report wire 归 `harness_common`。这些 harness-neutral seam 不收进 e2e 包。

## 关键约定

- **主链路**：`case.yaml → engine.run_cases → CasePlan(execute/judge) → BaseRunner → Outcome → CaseRun → verdict.json`；CaseRun 是 common Execution 的 E2E 子类并保留 OperationRun。复杂测试可直接使用完整 `prepare → execute → judge → cleanup`，cleanup 总执行且失败进入 Verdict error。
- **Experiment**：公共身份是具名验证意图；E2E 子类增加 `service + caseset`。单一配置或单一 Arm 仍然是 Experiment，不以是否存在对照组为前提。
- **Kernel 对齐**：Runner `Outcome` 是 Observation，一次 CaseRun（`case_id + variant`）是 Unit；一组可复判 CaseRun Unit 构成 Dataset，本次选择的 assertion / deterministic judge / soft metric 直接定义评估侧重点。立即执行并直接写 Verdict 可以融合这些阶段，但必须保留 Outcome、来源 identity 和实际组件配置，使判断能够离线重放；详见 [`../../../docs/kernel.md`](../../../docs/kernel.md#dataset-与反复评估)。
- **case 两个编写前端**：手写 canonical CaseSet，或 `@case/@spec` NL marker 经 `casegen compile` 编译（`casegen check` 是无-LLM intent-hash 漂移闸门）。`binding.symbol_id + optional spec_id` 对齐 spec-case 的 plural `specs[]`；未填 assertion 的草稿是 error，不会假绿。
- **Outcome 是 runner↔judge 契约**：`status_code / headers / body / duration_ms / metadata / raw`；SSE events 进 `metadata['events']`，engine 的 `response_view` 暴露 `events[]` / `event_count`。
- **Config**（`config.yaml`，语言无关 schema）：`service.{name,component,environment,base_url,headers}`，其中当前 environment 默认按 `KubernetesEnvironment{name,kubeconfig,context}` 解析；另有 `runtime.*_timeout` / `discover.{source_root,test_root}`（discover/casegen 用）。harness 不内置服务专用 header 名。
- **verdict**（`spec/verdict-schema.yaml`）：`e2e run` 跑完落 `runs/<scope>/<run-id>/verdict.json`（scope 默认 CaseSet 名，即 Experiment 名）。

- **当前范围**：服务 API 契约测试；Playbook / Script 与 Web、移动端、产品 API Target 的长期边界见领域设计。
- **跨语言对齐**：Python 与 Go 共享 CaseRun 生命周期、阶段 budget、cleanup 和 Verdict 语义，由仓库根目录 `conformance/e2e/` fixture 约束。

## References

- [E2E、Playbook 与 Target 设计](../../../docs/e2e-harness.md)

- 判定即数据架构（engine / 多编写前端 / 新老收敛）：[`../../../docs/case-unification.md`](../../../docs/case-unification.md)
- casegen（NL marker → 结构化 case 的 build-time 编译器）：[`../../../docs/casegen.md`](../../../docs/casegen.md)
- 跨语言约定（case/config schema）：[`../../../spec/conventions.md`](../../../spec/conventions.md)
- 统一判定出口：[`../../../spec/verdict-schema.yaml`](../../../spec/verdict-schema.yaml) + conventions.md「Run 产物与 verdict 出口」
- 接入示例：[`../../../examples/api-test/`](../../../examples/api-test/) / [`../../../examples/agent-test/`](../../../examples/agent-test/)
