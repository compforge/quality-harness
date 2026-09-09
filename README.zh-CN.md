# quality-harness

**测试软件与 Agent，让运行证据形成可追溯的质量结论。**

[English](README.md)

quality-harness 提供 API 测试、Agent 效果评测、性能测试、调用链分析和 Agent 轨迹分析 SDK。你可以运行项目自身的测试，也可以直接分析已有运行记录，产出供开发者、CI 和 Agent 工作流使用的报告与机器可读结论。

仓库当前提供领域 SDK、共享结果契约和平台工具箱。长期计划实现项目级 Harness，发现项目已有的质量能力、选择验证方式，并解释结果与覆盖缺口。具体见 [Quality Harness 设计](docs/quality-harness.md)。

## 可以验证什么

| 问题 | 能力 | SDK |
|---|---|---|
| 服务 API 是否符合预期？ | **e2e** — 执行用例，验证 API 契约 | [Python](docs/e2e-harness.md) / [Go](sdks/go/) |
| Agent 产出质量如何？ | **eval** — 评估结果，比较实验效果 | [Python](sdks/python/eval_harness/README.md) |
| 系统在压力下表现如何？ | **perf** — 按声明的目标衡量延迟、吞吐和资源用量 | [Python](sdks/python/perf_harness/README.md) / [TypeScript](sdks/typescript/perf-harness/README.md) |
| 调用链在哪里出现异常？ | **trace** — 分析 span，定位异常并辅助归因 | [Python](docs/trace-harness.md) / [TypeScript](sdks/typescript/trace-harness/README.md) |
| Agent 的决策与行动是否有效、高效？ | **trajectory** — 测量成本、发现模式、验证行为 | [Python](sdks/python/trajectory_harness/README.md) |

每类 SDK 回答一个独立的质量问题。当前 e2e 聚焦服务 API；trace 和 trajectory 可以直接分析已采集的运行证据。

## 如何使用

准备项目的用例与验收标准，或希望分析的运行记录，再根据要回答的问题选择 SDK：

```text
执行用例或实验 → 采集运行证据
导入已有记录   → 复用运行证据
                         ↓
                  分析、测量与验证
                         ↓
                     报告与结论
```

**Case** 描述可复用的测试输入与预期；**Dataset** 保存证据和标注，供反复评估；**Verdict** 以机器可读形式记录质量结论。证据与评估结果分开保存后，可以对同一数据集应用不同检查，无需重新运行被测系统。

项目负责自己的用例、测试动作与验收标准，部署工作流提供目标环境和凭据。quality-harness 提供执行、分析与报告机制；共享 Case 格式由 [spec-case](https://github.com/compforge/spec-case) 定义。

需要操作环境的测试可以使用[平台工具箱](docs/toolbox.md)，通过 Python 或 Go 控制、观察 Kubernetes 工作负载。目标资源、恢复条件和性能标准由项目决定。

## 快速开始

选择接近自身场景的示例：

| 示例 | 适用场景 |
|---|---|
| [API 用例](examples/api-test/README.md) | 数据驱动的请求与断言 |
| [Python 服务测试](examples/python-service/) | 包含准备、多步操作和清理的测试 |
| [Go 服务测试](examples/go-service/) | 通过 `go test` 执行用例并聚合结论 |
| [Agent 评测](examples/agent-test/README.md) | 数据集驱动的质量评估 |

运行 Python API 示例前，需要检出源码并安装 Python 3.11+ 和 `uv`。按目标服务调整示例用例与服务地址，然后执行：

```bash
cd sdks/python
uv sync
export WIDGET_BASE_URL=http://localhost:8080
export WIDGET_TOKEN=...
uv run e2e run ../../examples/api-test/cases.yaml \
  --config ../../examples/api-test/config.yaml \
  --runs-dir ../../runs
```

目标服务需要已启动，并提供用例使用的接口。结果写入 `runs/` 下的运行目录，其中包含 `verdict.json`。跳过的用例和执行错误会在结果中明确体现。

运行 Go 示例时，先按[示例说明](examples/go-service/) 配置已部署的服务，再从仓库根目录执行：

```bash
cd examples/go-service
export ASANDBOX_BASE_URL=http://localhost:8090
export EXAMPLE_TOKEN=...
go test -tags=e2e -v ./...
```

## 深入阅读

- [共享概念与契约](docs/kernel.md)
- [项目级 Quality Harness 设计](docs/quality-harness.md)
- [平台工具箱](docs/toolbox.md)
- [开发与测试](AGENTS.md#开发与测试)

quality-harness 仍处于早期公开阶段。各语言 SDK 的能力覆盖有所不同，可通过上面的能力指南选择实现。
