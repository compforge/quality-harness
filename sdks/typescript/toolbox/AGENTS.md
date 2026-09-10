# TypeScript 平台工具箱

## 项目定位与边界

`@compforge/harness-toolbox` 提供与消费方无关的基础设施 Client、DataSource、Transport 和有界数据采集。
命令、Plugin 协议、Case、Verdict、环境配置约定与业务查询归消费方；本包不依赖领域 Harness。

## 代码地图与核心模块

```text
toolbox/
├── src/client*.ts       # 初始化、借用、并发复用与幂等销毁
├── src/datasource.ts    # 客户端身份、构造与连接信息解析契约
├── src/concurrency.ts   # 可取消的共享容量
├── src/transport/       # TCP、端口转发与 Pod Python 路径
├── src/kubernetes/      # 集群访问、日志源共享与字节预算
├── src/mysql/          # MySQL 原生与 Pod 协议执行
├── src/redis/           # Redis 连接与拓扑
├── src/opensearch/      # OpenSearch 访问
├── src/process/         # Node-compatible 进程原语
├── scripts/build.ts     # 保留共享模块身份的多入口 ESM 构建
└── tests/               # 生命周期、访问契约及独立 Node 包验证
```

## 关键约定

- 根调用方拥有 ClientManager 和销毁权；子调用方只借用 ClientProvider。按稳定 DataSource key 合并初始化，
  初始化失败完成清理后允许重试，dispose 幂等且等待进行中的工作退出。
- 日志并发、字节预算和外部访问期限由调用方显式提供。不同数据源可共享根容量，不能按子调用重建池。
- 只在连接建立失败时选择另一 Transport，不重放可能已执行的协议操作。
- Python/Go/TypeScript 对齐已有公共行为语义，各自可以有不同能力覆盖；不为目录对称补实现。
- 发布内容只有构建后的 ESM、类型声明和 README；测试运行于 Bun，独立包 smoke 必须在 Node 下验证。

## 开发与测试

从本目录运行 `bun install --frozen-lockfile`、`make lint`、`make test` 和 `make build`。
包版本独立维护；同时按仓库要求提升根 VERSION。

## References

- [平台工具箱契约](../../../docs/toolbox.md)
