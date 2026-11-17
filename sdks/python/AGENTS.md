# Python SDKs

## 项目定位与边界

一个 uv workspace 管理 `quality-harness`、`harness-common` 与 `harness-toolbox` 三个独立分发包。
领域 SDK 依赖 common/toolbox；toolbox 依赖 common，common 不反向依赖 toolbox。每个包只打包自己的命名空间。

## 代码地图与核心模块

```text
python/
├── e2e_harness/        # 服务 API 契约测试
├── eval_harness/       # Agent 效果评测与对照实验
├── perf_harness/       # 性能、容量与资源画像
├── trace_harness/      # 调用链分析
├── trajectory_harness/ # Agent 决策与行动序列评估
├── common/            # 独立 harness-common：system/access/execution 契约与 report_kit
├── toolbox/           # 独立 harness-toolbox：协议客户端、LLM、Transport 与平台适配
├── pyproject.toml     # 包、依赖、CLI 与测试发现
└── Makefile           # 工作区构建、版本与报告资源准备
```

## 关键约定

- 五个领域 SDK 互不 import；公共能力集中在中立共享包。`harness_common` 统一执行事实与输出契约，各 SDK 自行拥有 runner、scheduler 和协议原语。
- 环境共享层只管理 EnvironmentFixture 与外层执行，Case / Trial 准备清理由各 Harness 拥有；见 [环境生命周期](../../docs/execution.md#环境与-case-的两层生命周期)。
- 测试贴近所属包，放在各包 `tests/`；默认 pytest 覆盖完整工程，发行包排除测试。
- 领域可选依赖通过 extras 声明，避免给其它 SDK 增加安装负担。
- Python 包版本使用本目录 `make bump`，同步 `pyproject.toml` 与 `uv.lock`；仓库整体版本按根目录约定独立更新。

## 开发与测试

从本目录运行：

```bash
uv sync --locked
make report-assets
repocli validate --repo ../.. \
  --component sdks/python/common,sdks/python/toolbox \
  --component sdks/python/e2e_harness,sdks/python/eval_harness,sdks/python/perf_harness \
  --component sdks/python/trace_harness,sdks/python/trajectory_harness \
  --check format,lint,test
make build
```

根 Makefile 不提供校验目标；七个叶子目录各自拥有 format/lint/test，复用同一个 uv 环境。
各入口只检查本目录，空的 `LINT_FILES` / `TEST_FILES` 表示该组件的完整范围。
局部验证可在叶子目录运行 `make test TEST_FILES=tests/test_example.py`；common/toolbox 的测试路径分别为
`harness_common/tests` / `harness_toolbox/tests`。`make fix` 是 `make format` 的别名，完成后再运行 lint/test。
从本目录运行 `uv run pytest -q` 仍可执行全部 Python 测试。报告资源在校验前通过 `make report-assets` 准备；
并发的叶子检查不再生成或删除共享资源。
构建产物写入仓库根目录 `dist/`。领域 smoke 命令与 fixture 使用方式归对应 Harness。
