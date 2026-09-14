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
├── common/            # 独立 harness-common：公共模型、Client/DataSource 生命周期、Verdict/LLM
├── toolbox/           # 独立 harness-toolbox 包，含 harness_toolbox/ 与 pyproject.toml
├── pyproject.toml     # 包、依赖、CLI 与测试发现
└── Makefile           # 测试、lint、格式化、构建与版本入口
```

## 关键约定

- 五个领域 SDK 互不 import；公共能力集中在中立共享包。`harness_common` 统一执行事实与输出契约，各 SDK 自行拥有 runner、scheduler 和协议原语。
- 测试贴近所属包，放在各包 `tests/`；默认 pytest 覆盖完整工程，发行包排除测试。
- 领域可选依赖通过 extras 声明，避免给其它 SDK 增加安装负担。
- Python 包版本使用本目录 `make bump`，同步 `pyproject.toml` 与 `uv.lock`；仓库整体版本按根目录约定独立更新。

## 开发与测试

从本目录运行：

```bash
uv sync --locked
make fix
make lint
uv run pytest -q
make build
```

局部验证使用 `make test TEST_FILES=<package>/tests`；toolbox 路径为 `toolbox/harness_toolbox/tests`。
局部格式化使用 `make fix CHECK_PATHS=toolbox/harness_toolbox`，完整门禁仍运行 `make lint`。`make fix` 会修改源码，完成后再运行 lint 和测试。
构建产物写入仓库根目录 `dist/`。领域 smoke 命令与 fixture 使用方式归对应 Harness。
