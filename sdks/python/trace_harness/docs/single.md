# 单 trace 分析与呈现

单条分析用于理解一次请求：经过哪些逻辑事件、有哪些观测、证据在哪里。共享模型见
[kernel](kernel.md)，数据准备与缓存见 [load](load.md)。

## 主流程

```text
定位 trace → 单成员 Dataset → tree → 分析 → 展示准备 → call stack / HTML / 下钻
```

```python
async with harness.open(source, work_dir="trace-work") as session:
    dataset = await session.select(SpanQuery(trace_ids=[trace_id]))
    async with session.tree(dataset, trace_id) as context:
        analysis = await session.analyze(context)
        await session.prepare_view(analysis, full=True)
        html = harness.render_interactive(
            analysis.trace, analysis.findings, measurements=analysis.measurements
        )
        output.write_text(html)
```

可指定 detectors / metrics；空列表表示不运行该类能力。需要单个字段或指标时通过 context
直接请求，不要求先运行全部分析。直接对内存数据建模的纯 assemble 仍可独立使用。

## 展示与证据

单条分析通常生成 HTML call stack。展示准备决定需要哪些 facts 和原文，renderer 只消费准备
好的数据。`full=True` 准备完整证据供离线详情面板使用；`full=False` 按投影依赖准备。
两者与 `LoadConfig.lazy` 独立，可以在 lazy 模式生成完整 HTML。

Node 的父子关系代表逻辑结构；Facet 的折叠、聚合、隐藏只改变 DisplayNode。Finding 仍可溯源
原 Node/span，重要信号不能因为折叠而消失。Measurement 的展示过滤不改变分析值。

AS 通过 AgentRun extractor 提供 agent/turn/model/tool 的业务投影，校验与渲染由 harness 执行。
分析快照支持离线渲染；保存部分证据时应保留覆盖状态，不能将缺失原文显示为完整空结果。

## CLI

```sh
trace single trace.jsonl --diagnose --html trace.html --work-dir trace-work
trace single trace.jsonl --no-lazy
```

trace-as 的 show、tree、diagnose、render-md 与 render-html 复用同一加载链，保留 AS 的解析、
结构修正、业务规则和展示贡献。HTML 及显式 probe 导出位置独立于临时缓存清理。


## Node detector 依赖

Node 与 Dataset 共用 [Detector 定义与依赖](kernel.md#detector-定义与依赖)。
`Detector(id="summary", requires=("detail",), detect=summarize)` 可以注册到
`TraceContributions.detectors`；`context.result("detail")` 读取同一 node 的依赖执行结果。
只选择 summary 会自动运行 detail。单条全量与指定规则共用执行器，前者额外运行 base/kind rules。

依赖结果按当前 node 隔离，节点之间保持后序；父节点通过 `context.findings` 消费子树观察，
不能通过 requires 假定另一 node 已有同名结果。失败可见于 findings 和 `analysis.detector_runs`，
保存分析快照后离线展示仍保留这些状态。重复分析重新执行规则，证据缓存与结果生命周期独立。
