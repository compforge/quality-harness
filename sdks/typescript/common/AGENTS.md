# TypeScript 共享内核

## 项目定位与边界

`@compforge/harness-common` 承载中立的 Client、ClientProvider、DataSource、ClientManager、EnvironmentContext、
Service、Component/Repository/Forge、Environment/Host、Workload/WorkloadInstance 与 Service 数据源关联。
Service 的身份与运行拓扑归 common；业务能力和诊断协议归消费方，具体协议、Transport 与平台适配归 toolbox。
Python 对应实现位于 `../../python/common`，各语言独立发布，保持已有公共行为语义一致。

## 代码地图与核心模块

```text
src/
├── client.ts          # 初始化与幂等回收契约
├── client-provider.ts  # Environment/DataSource 共同的客户端标识与构造契约
├── datasource.ts      # 数据来源语义与 Service 关联
├── context.ts         # 执行期环境、借用入口与预算，不依赖 Fixture
├── client-manager.ts  # 根执行内共享客户端与回收
├── workload.ts        # 负载声明与运行实例，不执行平台 I/O
├── service.ts         # Component 在 Environment 中的具名运行体现及 Workload 归属
├── component.ts       # Repository 内的可构建组件
├── repository.ts      # Forge 内的代码仓身份
├── forge.ts           # 代码托管系统身份
├── environment.ts     # 命名环境与 HostEnvironment，不隐式提供客户端
├── host.ts            # 环境的可选 local / SSH 访问主机声明
└── index.ts           # 公共入口
```

## 关键约定

- common 仅依赖标准库，不能反向依赖 toolbox、Doctor 或领域 Harness。
- Service 关联不改变 DataSource 身份；根执行持有回收权，消费者只借用客户端。
- 消费方扩展 Service 和 Environment，不复制其身份模型；静态声明不执行发现，也不证明目标存在。
- 保持 Node ESM 发布产物可用；使用 `make lint`、`make test` 验证，版本独立维护。

`src/execution.ts` 定义 ExperimentRun → Execution → OperationRun → Outcome 的公共事实骨架；领域只扩展自己的执行数据，不在 common 放调度器或 Judge。
