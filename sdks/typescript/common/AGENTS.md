# TypeScript 共享内核

## 项目定位与边界

`@compforge/harness-common` 承载中立的 Client、ClientProvider、DataSource、ClientManager、EnvironmentContext、
Service、Product/Component/Repository/Forge、Environment/Host、Workload/WorkloadInstance、Resource/ResourceSnapshot 与 Service 数据源/资源关联。
Service 的身份与运行拓扑归 common；业务能力和诊断协议归消费方，具体协议、Transport 与平台适配归 toolbox。
Python 对应实现位于 `../../python/common`，各语言独立发布，保持已有公共行为语义一致。

## 代码地图与核心模块

```text
src/
├── system/       # 产品/代码身份、运行对象、资源与离线知识
│   ├── product.ts / component.ts / repository.ts / forge.ts
│   ├── service.ts / environment.ts / host.ts
│   └── workload.ts / resource.ts / knowledge.ts
├── access/       # 获取事实所需的访问契约和客户端生命周期
│   ├── client.ts / client-provider.ts / client-manager.ts
│   └── datasource.ts / file.ts / context.ts
├── execution.ts  # 执行事实骨架，领域拥有调度与判据
├── identity.ts   # 由具体模型定义范围的身份契约
└── index.ts      # 唯一包级公共入口，目录调整不改变导出
```

## 关键约定

- common 仅依赖标准库，不能反向依赖 toolbox、Doctor 或领域 Harness。
- Service 关联不改变 DataSource 身份；根执行持有回收权，消费者只借用客户端。
- 消费方扩展 Service 和 Environment，不复制其身份模型；静态声明不执行发现，也不证明目标存在。
- 保持 Node ESM 发布产物可用；使用 `make lint`、`make test` 验证，版本独立维护。

`src/execution.ts` 定义 ExperimentRun → Execution → OperationRun → Outcome 的公共事实骨架；领域只扩展自己的执行数据，不在 common 放调度器或 Judge。
