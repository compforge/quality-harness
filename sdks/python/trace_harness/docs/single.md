# Trace Harness：单条分析

本文说明 Python Trace Harness 的单条分析流程、度量口径与展示设计。
通用概念和职责边界见 [Trace Harness](../../../../docs/trace-harness.md)，
跨 trace 的数据集、聚合、版本对比和资源控制见 [batch 分析](batch.md)。

## 1. 单条分析流程

```text
Source.fetch(trace_id) / 离线文件
  → normalize：原始 span → NormSpan
  → assemble：识别、认领、仲裁、构造 Node、连接父子关系
      → transform 投影所需 facts → brief
  → measure：计算本轮 Measurement
  → diagnose：读取 facts、Measurement 和已产 Finding
  → render：投影为 Markdown / HTML / 调用栈
  → extract：按需提取 AgentRun IR
```

构建、分析和展示分别可用：调用方可以只查看模型，也可以只运行 measure。
`harness.analyze(ctx)` 提供组合分析；renderer 只读准备好的结果，不能触发 transform、
measurer 或 detector。展示选择不能改变分析快照中的数值和发现。

### 采集与建模

Source 负责读取原始遥测，normalize 统一物理字段，assemble 负责逻辑事件与父子结构。
采集层不能提前合并不同请求的属性或替分析层判断 kind，否则会丢失证据归属。
HTTP client / server 等关联 span 应在逻辑建模时保留双方身份，供度量选择和证据回查。

大字段通过 light 采集与按需原文读取控制成本。light 表示字段投影，不能据此推断拓扑完整：
查询命中的 span 子集与一条 trace 的完整轻量 span 集是不同输入。
需要的属性或关系未采集时，不能把无法计算的结果解释为零或正常。

### FactTransform

从请求 facts 生成 curl、从关联调用提取 http_status，都属于 fact → fact 转换。
投影声明所需输入；消费方也可以显式请求额外 fact。TransformContext 管理依赖解析与缓存，
一个生产者的多个输出一起物化，同一 trace 内复用，不跨 trace 共享缓存。

基础 facts 和父子关系是固定输入。转换只添加新 facts，不覆盖已有值；多个适用生产者、
循环依赖或覆盖基础 facts 都是声明错误。一批请求全部成功后才写入，失败不留下部分结果。
curl 和结构化输入等已物化结果可随 facts 保存，离线查看无需重新执行转换，也不执行生成的请求。

### Measurement

Measurement 回答“发生了多少工作”，不自带告警阈值。定义包含 scope、单位和维度，
结果保留状态与证据，避免将未适用、失败或缺失当成数值零。

以现有累计度量 `calls_until_node_end` 为例：窗口从当前 trace 最早观测开始到锚点 node
结束，包含其他分支。已开始的调用计入次数，跨越窗口结束的调用只累计截至锚点的耗时。
每个 kind 分别提供调用次数、duration sum 和覆盖时间；HTTP client / server 配对只计一次，
保留调用方时间和双方证据。service 残余节点与展示 Group 不计为调用。

duration sum 是各调用耗时之和；覆盖时间是调用区间的并集。并发调用会让前者大于后者，
model 内嵌 HTTP 也会让不同 kind 的覆盖重叠，不能将这些数值相加解释为总 wall-clock。
`self_ms` 则是 node 区间中未被直接子节点覆盖的时间，不自动等于 CPU 时间或某个初始化开销。

累计度量按 kind 建立时间索引，各 node 复用查询结果；证据通过共享调用索引和窗口引用，
避免逐节点扫描并重复保存此前的所有 span。业务请求起点与 trace 最早观测起点可能不同，
“请求开始到某阶段完成”的指标必须声明自己的锚点，不能直接更名复用 trace-prefix 值。

### Detector 与 probe

判读包括 kind 规则、拓扑异常、分布离群、时序趋势和行为模式。确定性 detector 读取模型、
本轮 Measurement 与已产 Finding；业务知识留在业务贡献中。detector 执行并不保证缺失的
遥测被补齐，也不保证诊断覆盖所有根因。

probe 用于将 prompt、completion、error 原文等写成可检查的 evidence；由 Host 在调用点
显式开启，默认关闭，不随 Plugin 导入执行。Finding 记录发现与证据，验收由显式 Policy / gate
另行判断。展示 top-N 或折叠只限制阅读量；批量出现率需要完整计数，规则见 batch 文档。

可确定性检查的已知问题应沉淀成 detector 和案例，文档保留设计理由、根因解释及需要外部
证据的判断。看到重复调用可以提出缓存或重试策略的假设，单条 trace 本身不能证明改动收益。

## 2. 展示与证据

Node 的父子关系由 assemble 唯一写入。view 按需构建只读关系索引，经 facet 投影为
DisplayNode；折叠、分组、隐藏和同构聚合都是展示动作，不修改分析模型。
业务声明 brief、layout 和子节点展示策略，统一引擎负责遍历、Finding 绑定和序列化。

Measurement 的计算与展示选择分开。域贡献的 `measurement_filter` 可以只展示有意义的
阶段锚点，detector 仍读取完整结果。HTML 和 Markdown 使用相同投影；详情将 Measurement
与 Facts 分区展示，耗时使用可读单位，维度值按表格呈现。

时间线展示顺序、并发与未被子调用覆盖的区间。按调用路径聚合的耗时图则展示累计工作量，
存在重叠或异步脱离时，不能据其宽度推断请求 wall-clock 或关键路径。

分析 artifact 分开保存 nodes/facts、Measurement 定义与结果、Finding 和原始证据引用。
离线加载应展示已保存的分析结果；重跑分析是新一轮执行，不能因为当前插件或规则变化，
静默改变同一份报告。原文按需读取，分析结论保留 node/span 锚点以支持回查。

### AgentRun 投影

业务 `agent_run_extractor` 从完整 Node 关系提取 run、turn、model、tool 和 operation；
Trace Harness 负责 IR 校验和统一渲染，不猜测某个 Agent Framework 的分轮及关联语义。
AgentRun 可包含 turn / operation，turn 可包含 model / tool / operation，operation 可递归，
tool / operation 也可含嵌套 run。所有语义投影保留 source node 和 span 溯源。

## 3. 代码入口

Python 实现入口：

- [`harness.py`](../harness.py)：作用域与贡献组合。
- [`ingest/`](../ingest/)：采集、归一和建模。
- [`model/`](../model/)：Node、关系索引、度量与分析快照。
- [`transform.py`](../transform.py) 与
  [`analyze/`](../analyze/)：事实转换、度量与判读。
- [`view/`](../view/)：统一投影和渲染。

完整代码地图与开发约定见 [Python SDK AGENTS](../AGENTS.md)；
跨 trace 执行和 corpus 入口见 [batch 分析设计](batch.md)。
