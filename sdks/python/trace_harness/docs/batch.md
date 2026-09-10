# Batch 分析

批量分析回答一批运行记录的共同模式、分布与差异；耗时只是一个应用场景。
共享模型与业务扩展见 [kernel](kernel.md)，读取与资源控制见 [load](load.md)。

## 选择与执行

```text
Source → select → Dataset → detect → Findings / Measurements → 聚合、对比、下钻
```

select 接收时间、属性、错误条件与明确 trace IDs，固定实际成员并记录查询和选择上限。
命中 span 用于决定 trace 是否入选，不能作为完整 tree 的替代。Dataset 与分析规则分开，
同一 Dataset 可用于不同问题。

```python
async with harness.open(source, work_dir="trace-work") as session:
    dataset = await session.select(query)
    result = await session.detect(
        dataset, detectors=["http_request_patterns"], metrics=["self_ms"]
    )
```

node detector 逐 trace 后序执行，batch detector 每 Dataset 执行一次：

```python
async def inspect_dataset(dataset, context):
    async for analysis in context.trees(dataset):
        for node in analysis.trace.nodes:
            value = await analysis.fact(node, "business_fact")
            # 汇总必要值，或通过 context.emit(finding) 增量输出。
    return []
```

业务贡献通过 `TraceContributions.batch_detectors` 注册。规则可以重新访问指定 tree 或读取
本 Run 的结果行，不需要直接处理远端存储。不要把迭代出来的全部 analysis 收集到内存。

## 结果与统计口径

逐 trace 保存标量 facts、Measurements、完整 Findings 和失败记录，保留 trace/node/span 溯源。
默认报告从磁盘数值表计算分组均值、p50/p95/p99，不平均各小批次分位数。
同时报告 selected/succeeded/failed，读取失败不能计作正常的空 trace。

普通 HTTP 慢请求与连续串行同 API 的规则在 batch 中保留完整召回；单条展示可限制呈现条数。
client/server 配对后计作一次请求，duration sum 不等于 wall-clock，父子重叠区间不能简单相加。
streaming、模型调用与普通 HTTP 的适用范围由协议证据和业务规则确定。

局部规则按 trace 流式执行；全局规则先持久化所需值，再统计或二次遍历。现有 corpus 的表算子
仍可用于显式物化的小规模分析；部分 legacy gates 会显式读取表，不代表所有 batch 路径都具有
相同内存边界。

## 对比、报告与下钻

`BatchResult.compare(baseline)` 比较相同 kind/name/metric 的统计值，包含样本数量与 p50/p95
变化。比较的前提是查询范围、业务版本、流量组成与指标定义可比；分布变化本身不能证明因果。

例如 memory KB client 优化，可选择改动前后相近流量，比较 HTTP 耗时与规则命中，并查看异常
样本的调用栈。客户端复用收益不能仅由总耗时降低推断，还需要控制下游负载和请求构成。

批量默认输出汇总 HTML，不为每个 trace 生成 HTML。发现异常后用相同 Dataset 的 trace ID 下钻，
复用证据缓存。Finding 是分析观察；只有显式 gate 才产生 pass/fail verdict。

CLI `trace batch experiment.yaml` 支持 source、select、load、detectors、metrics、diff 与 gates。
trace-as 的 batch 入口负责环境、凭据和 AS 查询条件，核心执行留在 SDK。
trajectory 的批量分析可以沿用选择与按需访问思想，但其业务模型与规则仍归 trajectory_harness。
