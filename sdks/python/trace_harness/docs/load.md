# Trace Harness 数据加载

加载层负责把逻辑数据需求变成可复用的证据读取。懒加载是加载策略之一，与 single / batch、
是否生成 HTML 相互独立。入口为 `TraceHarness.open(source, work_dir=..., config=LoadConfig(...))`。

## 数据准备与访问

```text
trace 引用 → 完整轻量骨架 → 业务 assemble → Node / tree
                                  ↓ 请求 fact / metric / 展示
                           依赖解析 → 内存 / 本地证据 → Source → 纯计算
```

select 只固定 trace 成员；tree 激活时取得该 trace 的完整骨架。骨架除身份、时间和父子关系外，
还包含通用协议及业务声明的分类、关联元数据。仅有 id/parent/time 不足以恢复业务 Node。
大字段通过稳定 span 引用读取，详情物化不改变已经确定的逻辑结构。

OpenSearch 使用异步连接池，按 distinct trace 分页选择。骨架请求通过 `_source` 与 nested
`inner_hits` 投影字段，详情批量查询同时限定 trace/span 及已观察到的存储身份。查询超时、分片
失败、游标不推进或字段截断明确失败，不把不完整结果当完整 trace。

文件 Source 将 JSONL 逐记录导入 SQLite；Jaeger UI JSON 按文件解析并索引，索引后释放原始对象。
大批量输入优先使用 JSONL，UI JSON 单文件解析仍需容纳该文件。再次读取复用索引。

## 加载策略

| 配置 | 行为 |
|---|---|
| `lazy=True`（默认） | 实际请求字段及其依赖时读取，允许合并并发请求 |
| `lazy=False` | 当前 trace 激活时预加载指定 fields；未限定则准备完整证据 |

lazy 控制何时读取；fields 控制预加载范围；并发、活动 trace 数和字节预算控制资源使用。
预加载不执行全部指标或 detector，也不一次加载完整 Dataset。预加载之外的动态需求仍走相同入口。
未消费字段的预加载失败不影响无关分析，需要该字段时明确失败。

需要完整详情的 HTML 可能读取与预加载相同的数据量。两种策略的分析语义相同，性能收益取决于
实际消费范围，不以 lazy 开关承诺固定加速比例。

## 本地缓存与生命周期

```text
workspace/
  datasets/<id>/
    manifest.json       # 查询、来源、数量与选择上限
    members.jsonl       # 固定 trace 成员
    index.sqlite        # 成员索引、骨架与按字段缓存的证据
    evidence/           # 显式导出的证据
  runs/<id>/
    manifest.json       # 输入 Dataset、规则、指标、配置及运行状态
    results.sqlite      # 可分组排序的数值结果
    *.jsonl             # trace、facts、measurements、findings、failures
    summary.json
    report.html
    verdict.json
```

缓存读取顺序为当前 trace 已准备的数据、本地证据、Source。字段缓存记录字段存在与否，空值与
字段不存在可区分。读取失败不会写成成功缓存。SQLite 事务提交缓存，避免留下部分写入。
同一事件循环轮次的字段请求合并，在途读取按 trace 协调；跨 trace 受统一并发预算限制。

来源身份隔离环境/后端/索引；每个 Dataset 拥有证据缓存，每个 Run 拥有计算结果。
派生 facts 与 Measurement 只在当前 trace 分析生命周期缓存，避免跨业务代码版本复用过期结果。

不指定 work_dir 时使用临时目录并在关闭时清理；指定目录则保留，`Dataset.load(path)` 可重新打开。
跨进程文件源复用索引需传 `index_dir`。独立报告须导出到有效目标目录，不能返回随后被清理的路径。
退出时取消未完成读取、释放计算任务和连接；磁盘证据不随 tree lease 释放而删除。

## 一致性与资源边界

数据源按 append-only 使用：select 固定 trace 成员，首次骨架读取固定已观察到的 span 集。
换业务字段投影时在相同 span 集补读，不重新纳入晚到 span；重新 select 新 Dataset 可刷新观测。
这不构成全局时间快照。未缓存证据被后端清理时明确报告缺失，已缓存证据仍可离线复用。

活动 trace 有数量限制，单 trace 有字节预算；超过预算明确失败。预算按已解码证据检查，HTTP
解码和 UI JSON 导入期间的瞬时分配仍需在性能验证中观察。batch 结果逐条落盘，不常驻完整树集合。
加载统计记录证据读取次数、字节和缓存命中；性能比较同时记录 wall-clock 与峰值内存。Run 的 loading 统计是本轮增量，
不累加此前分析；OpenSearch Source 另记录全部 HTTP 请求与响应字节。

## 接口迁移

Source 统一为 async，删除旧 Fidelity、drop_attrs 和 Cohort 接口。`select` 只返回唯一 trace IDs，
`fetch(trace_id, fields)` 返回完整投影骨架，`read(refs, fields)` 补读证据，`aclose` 释放连接。
纯内存 assemble / measure 保持同步；涉及依赖准备的 analyze / diagnose 由调用方 await。
OpenSearch 可接收系统信任或自定义 `ssl.SSLContext`，凭据与私有 CA 由消费方提供。
