# Python 共享内核

`harness-common` 提供系统描述、访问契约和执行证据，不依赖 toolbox、领域 SDK 或第三方运行库。
具体 HTTP、LLM、数据库与平台访问属于 toolbox；领域 Harness 拥有调度和判据。

```text
harness_common/
├── system/       # Repository/Component/Product、Service/Environment、资源、部署契约
├── access/       # Client/DataSource、EnvironmentContext 与环境 Fixture 生命周期
├── execution/    # Experiment/Execution/Operation、Outcome/Artifact、Reducer/Verdict 与 run 布局
├── report_kit/   # 中立报告结构和 HTML 渲染，独立于执行生命周期
├── identity.py  # 身份协议
├── __init__.py  # 稳定包根导出
└── tests/       # 跨模块契约与 conformance 测试
```

`system` 描述目标，`access` 获取事实，`execution` 组织与解释证据。它们是包内职责分组，
不是独立分发包；跨组引用必须指向具体模块，避免依赖包根聚合导出造成循环。
包根保持既有符号和类型身份；深层导入迁移见 [0.6 升级说明](README.md)。
从 `sdks/python` 运行 `make fix`、`make lint`、`make test` 和 `make build`。
