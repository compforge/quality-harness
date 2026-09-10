# quality-harness

**测试软件与 Agent，让运行证据形成可追溯的质量结论。**

[English](README.md)

quality-harness 提供 API 测试、Agent 效果评测、性能测试、调用链分析和 Agent 轨迹分析 SDK。你可以运行项目自身的测试，也可以直接分析已有运行记录，产出供开发者、CI 和 Agent 工作流使用的报告与机器可读结论。

## 可以验证什么

| 问题 | 能力 | SDK |
|---|---|---|
| 服务 API 是否符合预期？ | **e2e** — 执行用例，验证 API 契约 | [Python](docs/e2e-harness.md) / [Go](examples/README.md#go-service) |
| Agent 产出质量如何？ | **eval** — 评估结果，比较实验效果 | [Python](sdks/python/eval_harness/README.md) |
| 系统在压力下表现如何？ | **perf** — 按声明的目标衡量延迟、吞吐和资源用量 | [Python](sdks/python/perf_harness/README.md) / [TypeScript](sdks/typescript/perf-harness/README.md) |
| 调用链在哪里出现异常？ | **trace** — 分析 span，定位异常并辅助归因 | [Python](docs/trace-harness.md) / [TypeScript](sdks/typescript/trace-harness/README.md) |
| Agent 的决策与行动是否有效、高效？ | **trajectory** — 测量成本、发现模式、验证行为 | [Python](sdks/python/trajectory_harness/README.md) |

## 快速开始

选择接近自身场景的示例：

| 示例 | 适用场景 |
|---|---|
| [API 用例](examples/api-test/README.md) | 数据驱动的请求与断言 |
| [Python 服务测试](examples/README.md#python-service) | 包含准备、多步操作和清理的测试 |
| [Go 服务测试](examples/README.md#go-service) | 通过 `go test` 执行用例并聚合结论 |
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
