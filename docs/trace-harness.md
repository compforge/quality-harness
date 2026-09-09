# trace_harness 设计文档

> 本文解释设计理由；跨语言的 normative contract 以
> [`spec/trace-harness.md`](../spec/trace-harness.md)、`schema/trace/` 和
> `conformance/trace/` 为准。Python 与 TypeScript 是对等实现。

## 0. 作用域与扩展边界

`TraceHarness` 是一套 trace 分析配置的作用域 owner；每个实例独立持有
specs / transforms / measurers / detectors / facets / agent_run_extractor。业务包或 Plugin 对外提供
`TraceContributions`，Host 在构造 harness 时显式合并。因此 import 顺序、包管理器
hoist 结果、bundle 中是否出现两份 harness，都不再决定业务语义。

这些贡献按输出责任划分：spec 从原始 span 建模；FactTransform 将已有 facts 转为新 facts；
Measurer 计算有 scope、单位、维度和证据的 Measurement；detector 据此输出 Finding；
facet 和 extractor 声明展示及语义投影。

`TraceContributions` 只承载确定性扩展；probe 会写 evidence，仍是 Host 在
`diagnose(..., probes=True)` 调用点显式开启，不随 Plugin 导入自动执行。
Harness 不再暴露模块级 `register_*` 扩展面。

trace_harness 是 e2e-harness 的第四个 Python SDK：**trace/span 分析框架**。前三个 harness
把系统当黑盒（发请求看响应），trace_harness 开盒——消费请求留下的遥测（OTel/Jaeger
span），回答"链路内部发生了什么、哪一层先反常"。它是另外三类的**归因补集**：e2e 失败
案例"为什么错"、eval 坏 case"模型实际收到/吐了什么"、perf 慢"时间花在哪一层"，都由
它回答——这是顶层 AGENTS.md 思想 #4"一次执行，多面观测"缺的最后一面。

按 quality-harness Kernel 语义，assemble 后的 Node 是 Observation，`trace_id + node_id` 定义 node-grain
Unit，nodes / corpus 构成可复评 Dataset；每次 EvaluationRun 记录所选 detector 与 gate，并生成带
detect 输出 Finding 的 Worksheet。若分析粒度提升到 trace 或 cohort，应建立对应 grain 的 Worksheet，不能把
不同含义的行混在一张表。更换 detector、gate 或报告侧重点时复用 Dataset，不重新抓取 trace。

| 问题 | 类型 | 视角 |
|------|------|------|
| 接口对不对 | e2e | 黑盒：发请求看响应 |
| agent 效果好不好 | eval | 黑盒：发请求评产出 |
| 压力下表现如何 | perf | 黑盒：发压看资源 |
| 链路内部哪一层先反常 | **trace** | **开盒：看执行轨迹** |

**一鱼两吃（两种消费模式）**：

1. **主动模式（驱动 + 采集 + 深查）**：开发者用 **common case 集**（`spec/` 的 canonical
   case，与 e2e/eval/perf 同源）有意发起一轮评测，框架发请求、逐 case 捕获 trace_id、
   随后深入链路细节——某个接口慢在哪层、入参拼装对不对（to_curl / prompt 落盘直接看）、
   效果问题由沉淀的判读自动归因。与 eval 的分工：eval 评**产出质量**（黑盒打分），
   trace 主动模式查**内部细节**（开盒归因）；同一份 case，两边各取所需。
2. **被动模式（法医）**：给一个 trace_id（某 env 某条出问题的 trace），单条分析——这是
   排障 skill（如 trace-as）的消费形态，也是分析引擎的最小入口。

两种模式共享同一套分析引擎；区别只在**采集入口**（主动 = case driver 现场驱动并捕获
trace_id，被动 = 按 id 回查遥测存储）。

**资产观对齐**：e2e 积累的资产是 case，trace 积累的资产是**判读知识**——每次人工从
trace 定位出一类坏模式，固化成 rule / detector / probe 进注册表；判读攒得越多，拿到新
trace 时机器自动指出的已知原因越多，人和模型只处理注册表没覆盖的新模式。主动模式让
两种资产互相放大：case 集喂出 trace 语料，判读注册表自动消化语料。

---

### Facts、Measurement 与 Finding 如何协作

用户看到一个 node 很慢，首先需要知道“到这里为止已经做了多少工作”，即使每次调用都很快，
汇总也可能占用不少时间。事实标准化与这类统计有不同生命周期，因此各自拥有输出。

| 概念 | 输出与用途 | 负责范围 |
| --- | --- | --- |
| KindSpec.build | node.facts 的基础字段 | 把 raw 协议字段转成标准化观察 |
| FactTransform | node.facts 的新字段 | 转换已有 facts，包括 http_status、curl、messages |
| Measurer → Measurement | 独立的定量结果 | 回答某个 scope 上的数量、耗时等问题，保留单位和证据 |
| Detector → Finding | 独立的诊断结果 | 读取 facts、Measurement 与已有 Finding，给出有依据的判断 |

Measurement 在 trace 与 trajectory 中都是“运行记录体现出来、但原始记录未直接给出的度量”。
两者面向不同的观察对象，共享这个作用和语义即可，各自维护适合自身的类型。

主链为 `assemble(build → transform 所需 facts → brief) → measure → diagnose → render`。
调用方也可以只运行 measure，直接查看统计。AnalysisContext 引用原 trace，并携带本次分析的
Measurement 与 Finding；重复分析不写 facts，也不复用前一轮分析结果。额外需要的 fact 由调用方
显式请求，Transform 物化到 node.facts 后再交给 renderer；渲染不会触发计算或执行生成的请求。

### 统一的 fact → fact 转换

请求转换成 curl，与子调用状态转换成父调用的 http_status，都生成新的具名 fact，使用同一个
FactTransform 契约。是否提前计算、是否作为详情展示，由消费方决定，Transform 不带执行时机
或 facts/details 分类。原始协议字段由建模层标准化，Transform 只读取已有 facts 和关系。

投影声明自己需要的 fact，装配在生成 brief 前计算这些输入。分析或用户按需查看时，通过显式
transform 入口请求名称；依赖解析和缓存由 trace 自己的 TransformContext 负责。一次生产者的
多个输出一起缓存，跨请求复用；不同 trace 的同名 node 不共享缓存。基础 facts 和关系是固定
输入，物化只添加新 facts，不覆盖已有值。

多个适用生产者、循环依赖或覆盖基础 facts 都是声明错误，直接报错，避免注册顺序改变事实。
一批请求全部成功后才写入事实；计算失败不会留下部分结果或污染缓存，后续请求可以重试。
已物化的 curl、结构化输入等随 facts 持久化，离线查看不依赖原始 span 或转换代码。

### 累计调用如何帮助解释慢

`calls_until_node_end` 的 scope 是当前 trace 的最早观测开始到锚点 node 结束，包括其他分支。
已开始的调用计入次数，尚未结束的调用只计截至锚点的已发生耗时。每个 kind 分别给出调用次数、
duration sum 与覆盖时间；后两者的差异揭示并发和重叠。HTTP client/server 配对只计一次，保留
调用方时间和双方证据。粗粒度 service 残余节点与展示 Group 不计为调用。

不同 kind 可以嵌套：model call 内的 HTTP 同时体现模型工作和网络工作，因此不能把各行相加
解释成总 wall-clock 或耗时归因。Measurement 展示“发生了多少”，Finding 再判断“是否反常”。
累计数额本身不隐含告警阈值。

每个 kind 扫描一次时间事件，通过活跃调用数的积分得到 duration sum，通过是否有活跃调用的
积分得到覆盖时间。各 node 复用索引查询，结果用窗口选择器引用共享调用证据，避免逐节点重扫和
重复保存此前所有 span。`self_ms` 也是 Measurement，描述节点区间中未被直接子节点覆盖的时间。

分析 artifact 将 nodes/facts、Measurement 描述和结果、Finding 分开保存。离线加载展示已经保存
的度量，避免规则版本或插件变化导致同一份报告的数值漂移。

## 1. 核心模型

一句话定调：**raw span 是物理事实，逻辑事件（node）是分析本体，特征列（facts/findings）是分析底座；
"树"只是看单条 trace 时的一种视图，不是被供奉的中心对象。**

这不是孤立发明，是观测系统的成熟共识——分析跑在扁平特征列上，结构只服务"看单条"和"抽特征"两个时刻：
- **Canopy**（FB SOSP'17，13 亿 trace/天）：events → **modeled trace**（异构原始事件归一成统一模型）→
  **feature lambda 抽特征** → **列式数据集** → Scuba 查询。modeled trace 是抽取阶段的瞬态结构，落盘的是列。
- **OTel Collector**（读过 core 源码 v1.60）：connector 是"既当 exporter 又当 receiver"的**信号桥接级**——
  消费一条流、**汇总后吐另一种信号**（`count` traces→metrics 出统计、`router` 扇出多管道），桥接之间不存
  特权中间对象。这正是 corpus 层的形态：消费 node 流 → 吐三表。其 spanmetrics / servicegraph connector
  （在 contrib）更进一步：**不建树**，只用成对父子关系就产 RED 指标与服务拓扑——印证 detectors 吃成对边、不持整棵树。
- **Honeycomb**：宽事件（扁平行）为本体，trace waterfall 是"由 trace_id 串起来的视图"；BubbleUp 在列上做分布对比。
- **Dapper 谱系 / Phoenix**：看单条用层级树，聚合分析导出列式 / DataFrame。
- **Langfuse**（OSS，读过源码 v3.184）：OTel span 经**优先级排序的 mapper registry** 映射成
  observation（一张**宽表**按 `type` 区分 GENERATION/SPAN/TOOL/AGENT/EMBEDDING/…），usage/cost 存
  **Map 列**，score 单独表按 `trace_id` + 可空 `observation_id` 双键挂——逐条印证本框架的
  `SpecSet.classify`（首个命中胜）、node＝observation 宽模型、facts/findings 列式。（注：本框架
  Finding 现只挂 node；trace 级结论落 traces 表与报告，Langfuse 式双键挂点等有真实生产者再扩。）
  反向亦印证隔离的必要：Langfuse 是**单体 processor + 十几条 per-field fallback**覆盖 13 套约定，
  其源码自承"拿关注点分离换兼容性"——本框架把厂商私货关进域 KindSpec，通用 spec 只认标准约定。

本框架与 Canopy 同构：`fusion+assemble` = modeled trace 建模阶段，`KindSpec.build` = feature lambda，
`三张表` = 列式数据集。流水线如下：

```
【span 流水线 · 固定】
  Source.fetch(trace_id) → raw span → normalize(NormSpan)
  → TraceHarness(contributions).assemble 七步 fusion ＝ Canopy「modeled trace」建模阶段
        ↓
【逻辑事件流（分析本体）】
  list[Node] + 父子边     —— node = 1..N 物理 span 熔成的一次逻辑调用，带命名 facts 列
  TraceContext（nodes 事实 + lazy 原文池 + specs 注册表 + evidence_dir）
  tree 只是 render/flame 要递归时按父子边「视图期惰性重建」的辅助索引，不是必经层
  NodeTreeExtractor<AgentRunIR> 由业务识别 run/turn/call/operation，通用包不猜框架语义
  AgentRun IR → 统一 renderer（可回溯 source_node_ids 与 span）
        ↓
【node 流水线 · 开放注册】
  四类判读生产者 → Finding 流：spec.rules / 拓扑 detectors / outlier+trend / probes(落盘证据)
  视图：text / html / series / flame；动作：to_curl / dump
        ↓                                        —— 以上 = 单 trace 分析
【corpus 流水线 · 纯数据流】                      —— 以下 = 跨 trace 分析
  N 个 ctx → 三张扁平特征表（traces / facts / findings，parquet）→ 语料算子 → corpus report
```

三条立场（决定所有边界怎么画）：

1. **node（逻辑事件）是一等公民，tree 退为视图期索引**。fusion 把异构 raw span 归一成 node 是
   建模动作（≈ Canopy modeled trace），node 才是分析单元——错误藏在卫星上、ttft 在主 span 上，
   不 fusion 则每个算子都要自己重拼"这算一次调用吗"。但 node 集是**平的**：diagnose 全部算子吃
   `iter_nodes`，obs_hole/detached 只用**成对**父子关系（同 OTel servicegraph）；真正需要递归整棵树
   的只有渲染缩进、火焰图路径、最近骨架祖先——全是视图时刻，按父子边现搭即可。**没有一个分析算子
   需要持有树。**
2. **跨 trace 层没有特权结构**。corpus 层是纯数据流：行是 node facts / findings / trace 摘要，
   算子是 group/aggregate/diff，duckdb/pandas 直接查。
3. **业务字段的脏，锁死在 KindSpec.build 这一道抽列闸**（= Canopy feature-lambda 隔离）。机制层只认
   两样：NormSpan 的 kind-无关骨架字段（id/parent/start/dur/service）、KindSpec 抽出的**已命名 facts 列**
   （`duration_ms`/`in_chars`/`http_status`）。raw 的业务字段（`gen_ai.<vendor>.*` 之类）到 build 为止、
   不向上游流动——report/diagnose/corpus 只见 facts 列名，永远不碰业务原始字段。这正是"raw span 有业务
   specific 字段、却不让它污染报表/诊断"的解法：定制全部关进抽列那一步，列一旦抽出，下游零业务知识。

kind 由此是横切两条流水线的语义包（**KindSpec**）：物理上按 kind 聚合（一 kind 一文件），逻辑上按
facet 分层，每个消费者的签名只声明自己的 facet：

```
KindSpec（semantic bundle）
  Assembly contract   matches / claims / build   → assemble 消费（识别+卫星认领+facts 抽列）
  Detection contract  metrics / strategy / rules → diagnose 消费（离群/趋势 + per-kind 判读）
  IR projection       project                    → assemble 烤入 Node.brief（renderer 只读 IR）
```

### 概念词汇

| 概念 | 一句话 |
|------|--------|
| NormSpan | 归一后的单 span（kind 无关字段统一形态），raw 留作溯源 |
| Node | **分析本体**：逻辑事件 = 1..N 个物理 span（primary + 卫星）熔成的一次逻辑调用，贫血 dataclass，带命名 facts 列（"度量填值 / 原文填指针"二分）+ parent 边 |
| facts 列 | KindSpec.build 从 raw 抽出的、已命名的轻量度量——业务字段隔离边界，下游只见列名 |
| Tree | **视图期惰性索引**，仅 render/flame/最近祖先等递归场景按父子边现搭；非分析必经层、非中心对象 |
| NodeTreeExtractor | 从完整 Node Tree 中确定性提取某个关注面 IR 的业务 pass |
| AgentRun IR | `AgentRun.items = AgentTurn / Operation`，`AgentTurn.items = ModelCall / ToolCall / Operation`；Operation 可递归嵌套 Operation，ToolCall/Operation 可包含嵌套 AgentRun 的 agent 语义中间表示 |
| TraceHarness | scoped 分析执行器；独立持有 contributions，并执行 IR 校验与统一 render policy |
| TraceContributions | 业务包或 Plugin 显式贡献的 IR 标准化、detect、关注面提取与声明式 render 扩展集 |
| TraceContext | 一次分析的承载对象：nodes 事实 + lazy 原文池 + specs + 运行时 |
| Finding | 统一的判读输出：node_id + source + severity + note（错误/离群/趋势/拓扑/probe 同流），render 读 Finding 上色 |
| KindSpec | 一个 kind 的语义包（≈ Canopy feature lambda 的载体），三 facet；注册进 SpecSet |
| Source | 采集协议：fetch(trace_id, mode) + query(window) → trace_ids |
| CaseDriver | 主动模式入口：吃 canonical case → 发请求 → 捕获 trace_id（TraceIdExtractor 可插拔）→ 交给 Source 回查 |
| corpus 三表 | traces（一行一 trace）/ facts（一行一 node）/ findings（一行一 Finding），parquet 特征列数据集 |

---

## 2. 流程

### 2.1 单 trace

```
cli single <trace_id|file> [--series kind:metric] [--curl span] [--html out]
  → Source.fetch → build_context（不自动判读）
  → analysis = harness.analyze(ctx, probes=…)   # 按需；probe 是唯一有副作用的环节，默认关
  → harness.extract_agent_runs(ctx)       # 有业务 extractor 时产出 AgentRun IR
  → harness.render_*(ctx, findings, measurements=analysis.measurements) / render_series
```

构建与判读分离：看树零副作用；`diagnose(ctx, probes=True)` 才写 evidence 文件。

### 2.2 主动模式：case 驱动的评测 + 采集（一鱼两吃之一）

```
canonical cases（spec/ 同源）
  → CaseDriver 逐 case 发请求（薄 httpx/SSE，按"先复制后收敛"自带，不 import e2e_harness）
  → TraceIdExtractor 捕获 trace_id（response header / SSE event；域钩子可插拔，
    如 AS 的 conversation→trace 反查由消费方注册）
  → 等遥测落库（可配 settle 延迟）→ Source.fetch → 单 trace 流水线逐条深查
  → 产物按 case 维度 join：case_id ↔ trace_id ↔ findings/facts
```

与 eval 的边界再强调一次：driver 只负责"把系统跑起来并留下 trace_id"，**不做质量打分**
——打分归 eval；本模式的产出是逐 case 的链路归因（哪层慢、入参长什么样、命中哪些判读）。

### 2.3 跨 trace / 批量（experiment 驱动，对齐 e2e-harness 惯例）

一个 experiment 一份 yaml，产物按 run 落盘，记录与渲染分离。`source` 四选一覆盖两种
消费模式与批量回查：

```yaml
# experiments/nightly-triage.yaml
source:
  # ① 主动：cases: { dir: cases/, target: { base_url: ..., profile: ... } }
  # ② 被动批量：opensearch + query（时间窗/过滤）或显式 trace_ids 列表
  # ③ 离线：jaeger_file
  # ④ 搭车：sibling_run（按任何姐妹 run 产物的 trace_id 列 join：eval result.csv / perf outcomes.jsonl）
  opensearch: { host: ..., index: "jaeger-span-*", auth: ... }
  query: { window: "2026-06-10", filter: ... }
specs: [trace_harness.kinds.genai, as_trace.kinds]               # import path，SpecSet 组装
analyses: [error_signature, fleet_outlier, diff: <run-id>]
probes_top: 20            # 漏斗：精查阶段对 top-N 可疑 trace 开 probe
gates:                    # 可选：声明才判定（→ verdict.checks[]）；没声明 → verdict=skipped
  max_error_findings: 0            # 本 run 无 error 级 Finding
  no_new_signatures: true       # diff 对基线无新增错误签名（回归门，需 diff 基线）
  fleet_outlier_ratio_max: 5    # 舰队离群倍数上限
```

```
runs/<experiment>/<run-id>/
  traces.parquet  facts.parquet  findings.parquet     # corpus 三表
  <trace_id>/findings.jsonl + evidence/               # 精查阶段的 per-trace 产物
  report.html                                      # report_kit 渲染
  verdict.json                                     # 统一判定出口（必产；gates → checks[]）
```

**漏斗式两段扫描**（成本设计）：粗扫 `fetch(mode=light)`（剥掉 prompt 等巨型 attr，度量
类 facts 不受损）→ 建树+判读 → 三表；corpus 算子标出可疑子集 → `fetch(full)` + probes
落证据 → 深分析。"度量填值、原文填指针"的二分在采集层复用一次。

### 2.4 corpus 算子（v0 三个）

| 算子 | 回答 | 来源案例 |
|------|------|---------|
| error_signature | 错误文案归一化聚类——"104500 aigw 504 ×6 / tpot ×4 / 104502 ×2"自动产出 | SkillsBench nightly triage 的人工聚类 |
| fleet_outlier | 同 (kind,name,metric) 跨 trace 建基线，找"这条的 planner 比舰队中位慢 3×"——单 trace 内不可见 | 单 trace outlier 的自然推广 |
| diff | 两个 run（两天/两次 nightly）对比，回归检测 | 发版前后对照 |

### 2.5 可视化

**单 trace**——调用栈视图（text/md/html 已有）之外，火焰图作为 tree 的另一种投影，两种形态：

- **时间线 icicle**（x=wall-clock time、y=深度）：看顺序与并发；fire-and-forget 子节点天然可见；
  父条上未被子节点覆盖的空隙即 obs_hole 的可视化；Finding 给条块着色（错误红/离群黄）。
- **聚合火焰**（同路径合并）：循环型 agent trace 的主力——百轮 planner 合成一根宽条，
  回答"哪条路径吃掉总时间"。注意异步分离会破坏"父时长 ≥ 子之和"的经典不变式，聚合
  按 path 自底向上求和布局，不按父时长。

两个出口：`folded stacks` 文本（speedscope / flamegraph.pl 直接消费，零成本互操作）+
HTML 走 report_kit IR（依赖 common，与 eval/perf 报告共用文档模型 + 渲染器，不手搓 CSS）。

**corpus 图表（v0 六个）**——渲染走 report_kit IR + 内联 SVG：

| 图 | 回答 | 表 |
|----|------|----|
| 错误签名 Pareto（按 env/model 堆叠） | 失败由哪几类主导 | traces |
| 耗时构成堆叠条（model/tool/空洞/其它） | 时间花在哪类环节 | facts |
| 舰队分位带 per (kind,name)（p50/p90/p99 + 离群点标 trace_id） | 谁慢得不正常 | facts |
| 轮次趋势带（loop node 按轮次聚合，中位带 + p90 带） | 第几轮开始变坏（舰队级） | facts |
| diff 龙卷风（两 run 签名/分位对比） | 发版前后什么变了 | traces+findings |
| 小时级热力（量 × 错误率） | 问题在何时聚集（与部署窗口共振一眼可见） | traces |

不进 v0：case×run 翻转矩阵（flaky 检测，等主动模式落地后加）。

---

## 3. 关键设计

### 3.1 为什么砍掉中间格式、只认 raw span

skill 时代存在"OpenSearch 侧筛选+扩展 → call-stack 记录"的有损中间层：字段级合并曾把
两个 http span 的 request 属性混在一起（to_curl 拼错 url 的事故根源），`span_kind` 预计算
把识别职责泄漏进采集层。本框架只认一种 canonical 输入——原始 OTel/Jaeger span；kind
识别完全收进 `spec.matches`（吃 raw attrs），三身份（骨架/卫星/残余）由 assemble 一手裁决。
体积问题交给 lazy 原文池（FullAttrsIndex：扫一遍建 span_id→偏移索引、按需解析单行）与
light 采集模式，不靠有损预处理。

### 3.2 为什么判读分四类、且 probe 单列

- **per-kind rules**（域知识跟着 kind 走）/ **拓扑 detectors**（纯函数吃 node 集 + 父子边，
  不持整棵树）/ **分布+时序**（outlier 引擎参数化 metric，ratio/topn 策略；trend 抓"每步都不
  离群但整体在变坏"的渐变恶化）——三者是纯计算，全部遍历平 node 集。
- **probe** 不判定好坏，把"该看的内容"（prompt/completion/error 原文）落盘成文件、路径进
  Finding note，判定交给读它的人/模型——效果问题的判定函数在数据外部，机器能做的是备料。
  probe 是判读层唯一有副作用的环节，故 `probes=False` 默认关、显式开启。

四类都只产 Finding；diagnose 汇流，render 统一消费 Finding。业务 detector 不直接操作
DisplayNode，业务 facet 也不重复实现判读。

### 3.3 为什么 corpus 三表是特征列、用 parquet

三表是 Canopy「列式数据集」的对等物——**node→facts 那一步（KindSpec.build）已经把业务字段抽成
命名列**，所以 corpus 层拿到的就是干净特征列，group/aggregate/diff/可视化全是列运算，不回头碰
raw、不认识任何 kind 的业务语义。这也是跨 trace 层"没有特权结构"能成立的前提：行是 node/finding
/trace 摘要，纯数据流。

格式上，facts 表在"一天所有 trace"量级可达百万行，csv 既慢又丢类型；parquet + duckdb 是机器
查询面的标准答案。与 e2e-harness 其它 SDK 的 csv 惯例差异点明：result.csv 是"人看的 per-run
摘要"，corpus 三表是"机器查询的分析底座"，受众不同、格式各取所需。

列形态再细分一层（校准自 Langfuse）：token/cost 是**开放维度**——input/output/total 之外还有
input_cached / cache_creation / output_reasoning… 各家各异、随时新增。Langfuse 用 `Map(String,UInt64)`
列存 usage_details/cost_details 而非固定列。本框架同理：facts 表只把**高频度量**（duration_ms /
in_tokens / out_tokens / http_status）固定成列，token/cost 明细走 map/long 形态，避免维度一多就爆列。

### 3.4 为什么 OpenSearchSource 放框架（optional extra）

它只是"按 trace_id/时间窗查 jaeger 索引"的通用参数版（host/index/auth 全是入参），不含
任何业务知识；放框架让任何 Jaeger-on-OpenSearch 用户开箱可用。依赖通过 extra 隔离
（`quality-harness[trace-opensearch]`），不给其它 harness 增重。**环境发现**（env
注册表、kubevpn、凭据解析）是 infra 知识，留在消费方（trace-as skill），它负责装配 Source。

### 3.5 判读知识的沉淀分界

通用判据（obs_hole / detached / trend / fleet_outlier——只依赖结构与度量）进框架；
域判据（aigw 网关超时锚点、sandbox 启动归因——依赖业务 attr 与排障知识）随域 KindSpec
包留在各业务仓/skill。沉淀约定同样拆两份：框架 docs 管通用判据的准入（少报误报、
summary 带数值、输出有界），域包管自己的案例库。

为什么"可机判的一律沉 detector"而不是写进排障文档：**detector 是 case as code，
召回方式是每次 diagnose 必然全量执行**——确定性召回，不依赖"模型在对的时机想起去翻
文档"，也不依赖检索相似度命中；案例越积越多时，文档/检索式召回的可控性单调下降，
而 detector 注册表的召回率恒为 100%，成本只随判据数量线性涨。由此推出分工：
判读知识凡能写成对快照的检查代码就进 detector，文档只保留 detector 表达不了的部分
（根因叙事、修复状态、需要快照之外数据的判断）。

### 3.6 与姐妹 harness 的关系

- 遵守仓库约定：与 e2e/eval/perf **互不 import**；`common`（report_kit / verdict / run 布局）是唯一共享。
- `source: sibling_run` 是一个**通用** source 概念：吃任何姐妹 harness run 产物里的
  trace_id 列做 join——eval 的 result.csv 与 perf 的 outcomes.jsonl（`Outcome.meta` 记
  trace_id，见 perf metric-model.md §8）是它的两个实例，不为某一家特化。坏 case / 慢
  Trial 自动附带链路归因，是"一次执行多面观测"的落地通路。
- **统一判定出口**：`trace batch` 的 run 目录与三家同契约必产 `verdict.json`。Finding 是
  发现（finding）、不直接变判定；可判定的是 experiment 里**显式声明的 `gates:`**（对三表
  /算子结果的断言，如"对基线无新增错误签名"），每条 gate → `checks[]` 一行（与 perf SLO
  check 同一种判定单元）；没声明 gates → status=skipped（按 perf 记录门原则，没验证过的
  run 不读成 green）。

### 3.7 数据挖掘：行为模式判读（behavior smells）

trace 语料不只服务排障——它还能挖掘 **agent 设计的系统性弱点**。判读资产由此分两个品类：

- **排障判据**（已落）：错误 / 超时 / 离群 / 断链——回答"这条 trace 哪坏了"。
- **行为模式判据**：时序/结构上的可疑模式——回答"tool desc / prompt / 参数 schema 哪写得不好"。
  关键差异：单条 trace 里它只是**嫌疑**（连调 3 次可能只是网络抖动），**跨 trace 聚合后才成结论**
  （"web_search 在 23% 的 trace 里被连续调 ≥3 次" → desc 或出参可用性有问题）。

模式目录（v0 候选，按"通用进框架、域专属随域包"的既有分界沉淀）：

| 模式 | 信号 | 嫌疑指向 |
|------|------|----------|
| tool_churn（**已落 v0**：`diagnose/patterns.py` + corpus `pattern_rates`） | 同一 tool 被同一父节点**背靠背**连续调用 ≥3 次（中间隔其它节点即断开——think→call 循环是正常形态） | tool desc 不清 / 出参格式模型用不了 |
| ping_pong | tool A→B→A→B 交替 | 两个 tool 职责边界不清 |
| 盲重试 | error 之后用**相同参数**原样重试 | 错误信息没有进上下文 |
| 参数重复 | 同 tool 同参数高频出现（跨轮次/跨 trace） | 缓存机会 / agent 在原地打转 |
| dead_tool | bot 挂载的 tool 在语料里几乎从不被选 | desc 让模型不会选它 |
| 轮次膨胀 | loop 节点轮次随会话长度超线性增长 | context 管理问题（已有 trend 判读的推广） |

**机制零新增**——这是现有流水线的自然扩展：单 trace 时序 detector（纯函数，吃 node 集 +
时序，`diagnose/patterns.py`）→ `Finding(source="pattern:…")` → findings 表 → corpus 的
`pattern_rates` 算子看"系统性 vs 偶发"（次数 / 命中 trace 数 / trace 占比，进 report
「行为模式」section）。行业锚是 **process mining**：
trace 语料就是事件日志，rework loop / ping-pong 正是流程挖掘的经典 motif。

**挖掘的输入是整个执行波次，不只 trace 平面**。一个波次（100 个 case 白天主动驱动 / 夜里
nightly）落下四个数据平面：verdict（pass/fail/error，e2e/eval 判定）、metrics（延迟/资源，
perf 观测）、traces（链路事实，opensearch）、findings（判读输出）。单平面的数字都不可行动
——"fail 23 个"不知道为什么、"churn 率 20%"不知道伤不伤；**跨平面 join 之后才出可行动的
结论**："fail 的 23 个 case 里 18 个命中 tool_churn → 改这个 tool 的 desc"。join 键全是
现成契约，无需新机制：`case_id == verdict.case_id`（Case 规范主键）、`trace_id` 列
（sibling_run）、`runs/<scope>/<run-id>/` 统一布局 + 四家同契约的 verdict.json。corpus 层
由此的长期形态：从"trace 三表"长成**执行波次的多平面挖掘底座**（摄入 verdict/metrics 两个
平面 + 跨平面关联算子）——落地仍渐进：先单平面 tool_churn，再跨平面 fail × pattern 关联。

**闭环到 eval**：挖掘的产出是**假设**（"改这个 tool 的 desc"），不是结论——改完用 eval A/B
验证（同一 case 集、改动前后两臂对比）。trace 出假设、eval 验证改进，与 sibling_run join
（eval 坏 case → trace 归因）互为反向，把"一次执行多面观测"扩成"挖掘 → 改进 → 验证"环。

### 3.8 长期方向：导出 RL Trajectory / Episode（只提一嘴，不进近期规划）

node 流水线的产物本质上已经是 agent 执行轨迹：model-call 的 prompt/completion 是
state→action、tool 结果是 observation、eval 分数与 findings 可充当 reward 信号。span 处理
工作流因此天然多一个出口——把一条 trace 投影成 RL 语义的 Trajectory/Episode（一个
exporter 而已，吃 ctx 产标准格式），供 SFT 轨迹筛选 / process reward / RL 训练消费。
case 集 → trace 语料 → 训练数据由此接通。形状等有真实训练侧需求再定，不预先建模。

---

## 4. 代码地图（规划）

```
sdks/python/trace_harness/
├── core/            # NormSpan / Node（一等）/ TraceContext / Finding / FullAttrsIndex
│                    #   tree 是 core.viewtree 里的视图期惰性索引（按父子边现搭），不是中心类型
├── spec.py          # KindSpec 三 facet + SpecSet（显式注册，无模块级全局表）
├── assemble.py      # fusion 七步（= modeled trace 建模）：route → claims → 仲裁 → build 抽列 → 连边 → 残余聚合，产 list[Node]
├── diagnose/        # __init__ 汇流 / outliers / series / detectors / probes
├── kinds/genai.py   # OTel GenAI 语义约定的通用 spec（model-call / tool-call / agent）
├── sources/         # jaeger_file / opensearch（optional extra）/ driver（主动模式：case→请求→trace_id）
├── corpus/          # 三表构建 + error_signature / fleet_outlier / diff
├── render/          # text / html / series / flame（icicle + folded stacks）+ report_kit 适配
└── cli.py           # single <trace>（被动）/ batch <experiment.yaml>（主动 cases 或被动批量，由 source 决定）
```

消费方分层（以 AS 为例）：trace-as skill 依赖本包，保留 AS kinds 域包（aigw/sandbox）、
环境发现与 Source 装配、trace_id↔conversation 反查、collect-logs——biz 留 biz。

---

## 5. 迁移路径（每步独立可用）

1. **机制层入仓**：core/spec/assemble/diagnose/render 自 trace-as skill 平移（去 call-stack
   依赖，normalize 直吃 jaeger doc），genai kinds + JaegerFileSource + 单 trace CLI + tests
   （用真实 trace 快照做 fixture 回归）。
2. **corpus 层**：三表 + 三算子 + runs/report_kit 接线。验收：把一次 nightly 的失败 trace
   批量跑成自动聚类报告，对照人工 triage 结果。
3. **skill 切换**：trace-as 改依赖 pip 包，AS kinds/采集留守，老 lib 删除；`make bump` 发包。
4. **主动模式（CaseDriver）**：`sources/driver`——吃 canonical case → 发请求 → TraceIdExtractor
   捕获 trace_id → 交 Source 回查 → 单 trace 流水线逐条深查（§2.2 的完整形态）。开篇"一鱼两吃"
   的主动半边以此步落地为准；**在此之前主动模式是规划，不是现状**。
5. **后置**：sibling_run join（§3.6）、行为模式 detectors + corpus 出现率聚合（§3.7，建议从
   tool_churn 起步）、day 级 query 入口、gates 扩展；Go 侧遵守"短期不跟进，形状稳定后批量同步"。

## 6. 决策记录

| 决策 | 结论 | 理由 |
|------|------|------|
| **谁是分析本体** | **node（逻辑事件）一等、tree 退为视图期惰性索引** | 行业一致（Canopy modeled trace 瞬态、OTel connector 不建树、Honeycomb 树即视图）+ 自查消费面：diagnose 全吃平 node 集，只视图时刻需递归树。否定候选 1（tree 中心）、修正候选 2（纯 span 流走到底会重新发明 node）、取候选 3 但把心脏从 tree 换成 node+列 |
| **业务字段隔离** | 锁死在 KindSpec.build 抽列这一步（= Canopy feature lambda） | raw 的业务 specific 字段抽成命名 facts 列后即止，report/diagnose/corpus 只见列名、零业务知识——这是"raw 有业务字段却不污染报表/诊断"的解 |
| OpenSearchSource 归属 | 框架，optional extra | 通用参数版无业务知识；env 发现留消费方 |
| corpus 表格式 | parquet 统一，定性为「特征列数据集」 | 百万行量级 + duckdb 查询；node→facts 即列，与 Canopy/Honeycomb 列式分析同形 |
| facts 表 usage/cost 形态 | 高频度量固定列 + token/cost 明细走 map/long | 对齐 Langfuse usage_details/cost_details Map 列：维度开放（cached/cache_creation/reasoning…），固定列会爆列 |
| 通用 spec 的别名口径 | 只收 OTel GenAI + OpenInference 两套标准约定 | 厂商私货（Vercel ai.*、genkit:* 等）留域 spec；Langfuse 单体多 fallback 自承"拿分离换兼容"，是反面教材 |
| 判读沉淀分界 | 通用进框架、域判据随域 kinds 留业务侧 | 与 KindSpec 的域/机制分层一致 |
| 中间格式 | 不引入（只认 raw span） | 有损合并的事故教训；识别职责归 spec.matches |
| tree 缓存 | 不做（且 tree 本就退为视图期产物，无可缓存的中心结构） | 实测 24k span 直跑 3.7s；复杂度不划算 |
| SemanticSpan 中间类型 | 不引入 | labeling/claims 是全局仲裁，逐 span 标注是幻影阶段；可解释性走 assemble --explain（后置） |
| 主动模式 driver | 自带薄 httpx/SSE，不 import e2e_harness | 仓库"先复制后收敛"约定；driver 只驱动+捕获 trace_id，不做质量打分（打分归 eval） |
| 原生 OTLP source 保真（后置，step 4+） | 读 pdata 的 Status / Events / typed Value，不从 jaeger-ism 反推 | OTel 规范里 error = `Status.Code==Error` + exception **Event**、attr 是 typed `pcommon.Value`（Str/Int/Double/Map/Slice…）；JaegerFileSource 现走 `otel.status_code` tag + `logs` 异常 + 扁平 tag，那是 jaeger ES 存储特例。OTLP source 落地时按 pdata 原义抽，归一收口在 source 层，NormSpan 骨架不变 |
| **Finding 与 verdict 的关系** | Finding（发现）不直接变 verdict（判定）；trace batch 必产 verdict.json，判定只来自显式声明的 `gates:` → `checks[]`，无 gates → skipped | 范畴区分：Finding 无预期可对照（语料里找出错误签名是分析的成功，不是 run 的 fail）；run 契约层则统一进四家共用的 verdict 出口（`Face` 扩 `trace`），沿用 perf 记录门诚实原则——没验证过的 run 不读成 green |
