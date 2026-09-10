# TypeScript trace-harness

## 项目定位与边界

本包是 Trace Harness 规范的 TypeScript 实现，供 TypeScript 消费方在不依赖
Python runtime 的环境中完成 trace 建模与渲染。规范、IR schema 与共享 fixture
分别在 `../../../spec/trace-harness.md`、`../../../schema/trace/` 和
`../../../conformance/trace/`；Python 与 TypeScript 是对等实现。

## 代码地图与核心模块

```text
src/
├── runtime.ts / dataset.ts     # Session、trace lease、固定成员与资源边界
├── ingest/sources/             # Source 协议及文件索引；后端访问边界
├── loading/                    # 证据复用、字段依赖与运行内计算
├── model/ / kinds/ / ingest/    # 同步建模与协议事实
├── transform.ts / analyze/     # 纯计算与异步依赖消费
└── view/                       # 只消费已准备结果的渲染
```

新增公开概念时先修改语言中立规范和 conformance case，再同步两端实现。

## 关键约定

1. `Node` 是分析本体，transform 与渲染复用 TraceContext 的只读父子关系索引。
2. 只有 `KindSpec.matches/claims` 改变结构；FactTransform、measure、diagnose 和 render 不得 re-parent。
3. 通用包不写具体业务域知识；业务通过 scoped `TraceContributions` 贡献
   spec、FactTransform、Measurer、Detector、Facet 和 `agentRunExtractor`，不依赖模块导入副作用。
   Extractor 从完整 Node Tree 产出 AgentRun IR（Operation/AgentRun 均可递归嵌套）；不接管递归和输出序列化。
4. Python 与 TypeScript 的公开 IR 字段保持同名，便于 fixture 与产物交叉验证。
5. Kernel 对齐：assemble 后的 Node 是 Observation，`trace_id + node_id` 是 node-grain Unit key；nodes / corpus 是可复评 Dataset，本次选择的 detector / gate 直接定义评估侧重点并由 EvaluationRun 记录，detect 输出 Finding。不同 Unit grain 使用不同 Worksheet；详见 `../../../docs/kernel.md#dataset-与反复评估`。

6. Session 统一管理 Source 生命周期和跨 Dataset 的读取预算；lease 释放后不能继续请求分析数据。
   持久化证据与运行内计算缓存生命周期不同。预加载只作用于当前 trace，不能改变分析或展示语义。

## 开发与测试

从本包目录运行：

```bash
bun install --frozen-lockfile
bun test
bun run typecheck
bun run build
```

## References

- `../../../spec/trace-harness.md` — 语言中立规范
- `../../../docs/trace-harness.md` — trace-harness 设计文档

- `../../../spec/trace-loading.md` — 共享加载语义与 conformance
- `docs/loading.md` — Source、依赖声明和资源限制
