# quality-harness

## 项目定位与边界

**初衷**：一个 AI 项目会越来越复杂——功能多、链路长、测试面广，经常临到发版才发现问题，慌慌张张地修，修完也不敢说系统就完全没问题。本仓库的回答是：把"系统健壮不健壮"拆成几个可以分开回答的问题，各建一类 harness——前三类是**黑盒测试**（发请求看响应），trace 与 trajectory 是两种**开盒分析**（分别看物理链路与 agent 行动过程）——

| 问题 | 类型 | SDK |
|------|----------|-----|
| 接口对不对 | API 测试（e2e，黑盒） | `sdks/python/e2e_harness` / `sdks/go/e2e` |
| agent 效果好不好 | 效果测试（eval，黑盒） | `sdks/python/eval_harness` |
| 压力下表现如何 | 压力测试（perf，黑盒） | `sdks/python/perf_harness` / `sdks/typescript/perf-harness` |
| 链路内部哪层先反常 | trace 分析（trace，开盒） | `sdks/python/trace_harness` / `sdks/typescript/trace-harness` |
| agent 的行动过程是否合理 | 轨迹评估（trajectory，开盒） | `sdks/python/trajectory_harness` |

**边界**：跨语言（Go + Python + TypeScript）测试框架聚合仓库，各语言 SDK 在 `sdks/` 下保持独立工程。框架不 import 被测服务的 internal 代码，纯黑盒（HTTP / SSE / DB-query 由服务侧自己包装）。

跨 Harness 的通用模型与 owner 分工见 [`docs/kernel.md`](docs/kernel.md)；e2e 的 Case 路径及 Playbook → Script → Web / Android / iOS / 产品 API Target 长期边界见 [`docs/e2e-harness.md`](docs/e2e-harness.md)。

当前交付的是领域 SDK、共享契约与平台工具箱；长期计划在本仓库实现项目级 Quality Harness，
负责理解目标、发现能力、选择验证和解释证据，设计见 `docs/quality-harness.md`。
上层 Harness 尚未实现。领域 SDK 同时支持主动测试与已有运行证据分析，不强制所有入口提供 Case。

## 核心概念与职责

Case 描述可复用的测试输入与预期，格式由 spec-case 持有；Dataset 固定运行证据与标注，供不同规则
反复评估；Verdict 是供人、CI 与 Agent 工作流消费的机器可读质量结论。证据与评估结果分开保存，
对同一 Dataset 更换检查规则时无需重新运行被测系统，详细契约见 [`docs/kernel.md`](docs/kernel.md)。

被测项目拥有用例、测试动作、资源生命周期与验收标准，部署工作流提供环境、凭据、目标版本和触发策略。
本仓库提供执行、分析、报告机制与共享平台工具；环境控制由消费项目选择目标、故障时机和恢复判据。
各语言 SDK 的能力覆盖可以不同，以各自使用指南为准；当前 e2e 聚焦服务 API。

## 代码地图与核心模块

各子模块的定位、代码地图、关键约定收敛在**各自的 AGENTS.md**，本文件不再展开；改某个 SDK 前先读它的 AGENTS.md。

```
quality-harness/
├── spec/                # 运行时约定层：case 兼容投影 / config / verdict / conventions
├── conformance/         # 跨语言共享行为 fixture
├── sdks/                # 各语言独立工程，共享根目录的契约与 conformance fixtures
│   ├── python/          # uv 工程；五个 sibling SDK + harness_common + harness_toolbox
│   │   ├── e2e_harness/        # API 契约测试 → 见其 AGENTS.md
│   │   ├── eval_harness/       # Agent 效果评测 → 见其 AGENTS.md
│   │   ├── perf_harness/       # 性能与容量测试 → 见其 AGENTS.md
│   │   ├── trace_harness/      # 调用链分析 → 见其 AGENTS.md
│   │   ├── trajectory_harness/ # Agent 轨迹评估 → 见其 AGENTS.md
│   │   ├── harness_common/    # 中立模型、Verdict、LLM 与报告能力
│   │   └── harness_toolbox/   # 环境操作与观测 → 见其 AGENTS.md
│   ├── go/              # e2e SDK + 平台工具箱 → 见 sdks/go/AGENTS.md
│   └── typescript/      # perf / trace SDK → 见各包 AGENTS.md
├── examples/            # 接入示例：api-test / agent-test
└── docs/                # 跨 SDK 设计文档
```

## 关键约定

- **文档分工**：`README.md` 与 `README.zh-CN.md` 面向读者，只保留产品定位、能力选择和最短接入路径；两者同步维护。目录组织、职责边界、开发约定和 Harness 实现规划归 `AGENTS.md`，详细模型与设计理由归 `docs/`，各场景的完整操作步骤归 SDK 或示例使用指南。
- **资产与执行分工遵循 Kernel**：稳定资产格式只有一个 canonical owner；quality-harness 负责运行机制、领域 Harness、Run 产物与 Verdict。通用约束见 [`docs/kernel.md`](docs/kernel.md)，Playbook / Target 领域边界见 [`docs/e2e-harness.md`](docs/e2e-harness.md)。
- **同一 CaseSet，多种执行视角**：Eval / Perf 直接消费 spec-case CaseSet；Experiment 只能选择 Case、设置 weight 或其它运行参数，不能复制或覆盖资产字段。跨语言约束由 `conformance/case/` 证明。
- **执行 / 采集 → Observation → Unit → Dataset → EvaluationRun / Worksheet → Report**：所有 Harness 都按这套顶层语义对齐。Dataset 固定可复用的 Unit facts；每次运行选择的 Detector、Evaluator、Measurer 与可选 Policy 直接表达评估侧重点，并在不重新执行 Case 的前提下为同一 Dataset 产生新的 Worksheet、Verdict 与 JSON / HTML Report。`detect / evaluate / measure` 是并列处理职责，输出 Finding、Evaluation 与 Measurement；Finding 不自动决定 Verdict。各 Harness 保留自己的 Unit grain、强类型 key、调度和聚合模型，详见 [`docs/kernel.md`](docs/kernel.md#dataset-与反复评估)。
- **Trajectory 专门化**：trajectory_harness 直接使用业界 ATIF v1.7 `Trajectory`，不拥有另一套轨迹格式；Harness 拥有 `Measurements / Detector / Verifier`。Measurements 从 Trajectory 确定性派生，二者共同输入 Detector 与 Verifier；后两者均可面向 `cost / effect`，也均可声明 `hard / soft` 规则，不暴露 Evaluator 概念。
- **可验证交付从开发期开始**：被测项目随需求、外部行为变更和缺陷修复维护 Spec / Case，再由 quality-harness 在部署后针对指定版本与环境执行为 Verdict。项目拥有验证资产与判定标准，部署领域拥有环境、凭据、触发和发布策略；API、CLI、Pipeline、Job 只是可替换适配。
- 五个 Python SDK 共享同一个 uv 工程与 `spec/` 约定，**互不 import**；公共能力集中在 `common`（运行时身份、ExperimentRun/Execution/OperationRun/Outcome、Reducer/Artifact、verdict、llm + report_kit）这一中立共享层，而不是 SDK 之间互相复用。common 统一执行事实而不统一 runner/scheduler 等执行机制。各 SDK 仍自带协议原语（Outcome 具体形状、runner、SSEParser；perf 自带 httpx 发压栈、trace 自带薄 driver）——**先复制后收敛**，确属公共再收进 `common`。trace 的 parquet 持久化走可选 extra `[trace-corpus]`，不给其它 SDK 增重。
- 新增能力先想清楚归哪类问题（对错 / 效果 / 容量 / 归因），落到对应 SDK；跨 SDK 的"公共抽象"冲动默认抑制，先复制后收敛，确属公共再进 `common`。
- Go/Python e2e 共享 CaseRun 语义（prepare/execute/judge/cleanup、阶段 budget、Verdict），API 保持各自语言习惯；资产模型仍统一由 spec-case 持有。
- Kubernetes 等[平台工具箱](docs/toolbox.md)按语言提供惯用 API，并共享操作与观测语义；它们不拥有
  Case、负载模型或 Verdict。e2e / perf 及消费仓负责具体目标选择、故障时机和通过条件。
- Go/Python e2e 的共同语义由 `conformance/e2e/` fixture 约束；Go 项目通过 `e2e/testrun.Run` 将一次 `go test` 中的 CaseRun 聚合到统一 run 目录，不在消费仓重复实现 Recorder/TestMain/Verdict 胶水。它是 Go testing adapter，不引入跨语言 Suite 概念。
- Trace Harness 的 canonical 定义是 [`spec/trace-harness.md`](spec/trace-harness.md) 与
  `schema/trace/v1/`；Python 和 TypeScript 是对等实现，共用 `conformance/trace/`
  fixtures。通用包不承载业务域知识，业务能力通过 scoped
  `TraceContributions` 显式组合。

## 开发与测试

```bash
# 测试在各 SDK 包内（<pkg>/tests/），共用 uv 工程；pytest 无参=全量（testpaths）
cd sdks/python && uv sync && uv run pytest -q
cd sdks/python && uv run pytest perf_harness/tests/ -q   # 只跑某个 SDK
cd sdks/python && make lint        # ruff
cd sdks/python && make bump        # patch 版本号 + uv lock（quality-harness 发布用）

# eval_harness 端到端（mock，无需 live server）
cd sdks/python && uv run python -m eval_harness.cli eval_harness/materials/experiments/smoke.yaml --mock --fresh --runs-dir /tmp/eh

# perf_harness 端到端（mock，无需 live server / 集群）
cd sdks/python && uv run python -m perf_harness.cli run perf_harness/examples/mock.yaml --out /tmp/ph

# trace_harness 端到端（离线 jaeger 文件 → 调用栈 + 判读；批量 corpus 用 trace batch <exp.yaml>）
cd sdks/python && uv run trace single ../../conformance/trace/fixtures/genai-basic.jsonl --diagnose

# Go（参考实现）
cd sdks/go && go test ./...

# TypeScript trace-harness
cd sdks/typescript/trace-harness && bun install --frozen-lockfile && bun test && bun run typecheck

# TypeScript perf-harness
cd ../perf-harness && bun install --frozen-lockfile && bun test && bun run typecheck
```

## References

- 暂缓能力与启动条件：[`docs/backlog.md`](docs/backlog.md)
- 跨 Harness 通用内核：[`docs/kernel.md`](docs/kernel.md)
- 跨 Harness 环境操作与观测工具箱：[`docs/toolbox.md`](docs/toolbox.md)
- e2e、Playbook 与 Target：[`docs/e2e-harness.md`](docs/e2e-harness.md)
- Quality Harness 的定位、发现式评估与异步触发模型：[`docs/quality-harness.md`](docs/quality-harness.md)
- e2e_harness（API 测试）：[`sdks/python/e2e_harness/AGENTS.md`](sdks/python/e2e_harness/AGENTS.md)
- Python 平台工具箱：[`sdks/python/harness_toolbox/AGENTS.md`](sdks/python/harness_toolbox/AGENTS.md)
- eval_harness（效果测试）：[`sdks/python/eval_harness/AGENTS.md`](sdks/python/eval_harness/AGENTS.md)，使用指南 [`sdks/python/eval_harness/README.md`](sdks/python/eval_harness/README.md)
- perf_harness（压力测试）：[`sdks/python/perf_harness/AGENTS.md`](sdks/python/perf_harness/AGENTS.md)，使用指南 [`sdks/python/perf_harness/README.md`](sdks/python/perf_harness/README.md)
- Perf 跨语言契约：[`spec/perf-contract.md`](spec/perf-contract.md) + [`spec/perf-run-schema.yaml`](spec/perf-run-schema.yaml) + [`spec/perf-outcome-schema.yaml`](spec/perf-outcome-schema.yaml)
- TypeScript perf-harness：[`sdks/typescript/perf-harness/AGENTS.md`](sdks/typescript/perf-harness/AGENTS.md)，使用指南 [`sdks/typescript/perf-harness/README.md`](sdks/typescript/perf-harness/README.md)
- trace_harness（trace 分析）：[`sdks/python/trace_harness/AGENTS.md`](sdks/python/trace_harness/AGENTS.md)，设计文档 [`docs/trace-harness.md`](docs/trace-harness.md)
- trajectory_harness（agent 轨迹评估）：[`sdks/python/trajectory_harness/AGENTS.md`](sdks/python/trajectory_harness/AGENTS.md)，设计文档 [`docs/trajectory-harness.md`](docs/trajectory-harness.md)
- Go SDK：[`sdks/go/AGENTS.md`](sdks/go/AGENTS.md)
- TypeScript trace-harness：[`sdks/typescript/trace-harness/AGENTS.md`](sdks/typescript/trace-harness/AGENTS.md)，使用指南 [`sdks/typescript/trace-harness/README.md`](sdks/typescript/trace-harness/README.md)
- 顶层导览：[`README.md`](README.md)
- 跨语言约定：[`spec/conventions.md`](spec/conventions.md)
- 统一判定出口（run 目录 + verdict.json，五家共用，devloop 消费）：[`spec/verdict-schema.yaml`](spec/verdict-schema.yaml) + conventions.md「Run 产物与 verdict 出口」
- 接入示例：[`examples/api-test/`](examples/api-test/) / [`examples/agent-test/`](examples/agent-test/)
