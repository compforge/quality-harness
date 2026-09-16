# TypeScript 共享内核

## 项目定位与边界

`@compforge/harness-common` 承载中立的 Client、DataSource、ClientManager 与 Service 数据源关联。
具体协议、Transport 与平台适配归 toolbox；业务 Service 定义和诊断协议归消费方。
Python 对应实现位于 `../../python/common`，各语言独立发布，保持已有公共行为语义一致。

## 代码地图与核心模块

```text
src/
├── client.ts          # 初始化与幂等回收契约
├── datasource.ts      # 配置身份与 Service 关联
├── client-manager.ts  # 根执行内共享客户端与回收
└── index.ts           # 公共入口
```

## 关键约定

- common 仅依赖标准库，不能反向依赖 toolbox、Doctor 或领域 Harness。
- Service 关联不改变 DataSource 身份；根执行持有回收权，消费者只借用客户端。
- 保持 Node ESM 发布产物可用；使用 `make lint`、`make test` 验证，版本独立维护。
