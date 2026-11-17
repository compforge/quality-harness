# 产物与报告

报告消费已经记录的执行或评估事实。领域负责归约与结果含义，公共层提供产物契约和中立呈现组件。
[执行](execution.md) 产生 Outcome，[评估](evaluation.md) 解释 Observation；渲染阶段不访问远端、重发请求或重新执行 Judge。

## 产物归约

Reducer 只读取 ExperimentRun 已记录事实并产出 Artifact。Artifact 具有逻辑名称和 run 目录内的
相对路径，内容与 schema 归领域所有。一个 Artifact 可以汇总多个 Outcome，同一份事实也可以生成
多个产物；精确 provenance 由 Execution identity、selector 或时间窗口表达。

Report 将一个或多个 Artifact 渲染给人。HTML、文本、CSV 等投影应保持来源与缺失状态，不能在
渲染时补造观测事实。各领域保留自己的聚合与归约语义。

## Verdict 与机器输出

Verdict 是领域结果的机器投影，保留结论、原因、指标和证据入口，供人、CI 和 agent 开发循环消费。
是否存在验收策略由领域契约决定；Perf 的执行状态与业务判断分别表达，见 [Perf 契约](../spec/perf-contract.md)。
通用状态与证据结构见 [Verdict schema](../spec/verdict-schema.yaml)。

执行完成、成功生成 HTML 和没有 Finding 都不能自动推导为业务通过。消费方根据实际覆盖的检查与
Policy 决定验收，报告应同时呈现发现、执行异常、证据缺口和未覆盖范围。

## 离线浏览器组件

`harness_common.report_kit` 提供中立报告模型与 Python HTML 适配，TypeScript perf 提供领域适配。
仓库 `report-kit/` 持有共享浏览器组件；语言适配器准备数据与宿主容器，组件持有 DOM、样式和交互。
Python 构建 hook 将资源写入 wheel / sdist，TypeScript 在构建时内嵌同一份源码。发布产物离开仓库后
仍能生成离线报告。构建、vendor 来源和测试入口见 [Report Kit](../report-kit/README.md)。

## Graph 呈现

Repository-owned `report-kit/graph/` renders already reduced GraphView data using offline Cytoscape and bundled
ELK 0.12.1. It does not query services or calculate domain metrics. Rendering keeps every node,
edge, label, group, detail and QPS-derived size supplied by the domain view.

### 布局与视口

Placement has two levels. Inside each named group, independent nodes use ELK rectangle packing
so parallel APIs occupy multiple columns. If the group has internal edges, it uses directed layered
layout instead. Ungrouped nodes remain individual boxes. The outer layered layout arranges these
boxes according to inter-group links; these temporary links are only positioning constraints.
The displayed graph retains the original Operation edges, including conditional edges and cycles.

The renderer compares four candidates: two local packing aspect ratios derived from the actual
viewport, each with outer RIGHT and DOWN flow. It chooses the candidate maximizing the scale
that fits the viewport. It never changes node dimensions to stretch the drawing or imposes a fixed
pixel width/height on the graph. Service grouping remains auxiliary; it does not replace Operations.

### 交互与异步布局

Only visible viewports are laid out. Frame changes and actual viewport resizes recompute positions;
manual pan/zoom keeps its view until that happens. Generation checks prevent stale asynchronous
results from overwriting a newly selected frame. Loading/failure state is visible, and image copying
is disabled while positions are pending. Full-image copy still exports the complete graph.

Call / may_call retain their arrow and solid/dashed styles; repetitive edge text is shown on hover
or selection. Other edge labels remain visible. Layout changes do not alter JSON graph facts or
metric identities, and do not prove execution of a static path.

The ELK bundle runs in-process without a remote worker, CDN or runtime package install. Its pinned
source, checksum and license are recorded in `report-kit/vendor/README.md`.

### Service 过滤

Callgraphs opt into group filtering with GraphView.group_filter_label="Service". Each frame declares
entry_ids before filtering; Perf uses the source Operations of the original directed topology
(nodes with no incoming call/may_call edge). Cyclic graphs without source Operations have no
inferred entry and show a disabled filter rather than choosing an arbitrary root. Callers can supply
explicit entry_ids for such GraphFrames. Entry IDs must exist in the selected frame.

The Service checklist defaults to all selected and persists across window changes. Entry Operations
are pinned even if their Service is unchecked. Traversal follows the original directed edges using
only allowed nodes, then removes every node unreachable from the entries. A shared descendant
survives when another allowed path reaches it; cycles terminate normally. No shortcut is invented.
The renderer only groups and lays out surviving Operations. Unchecking the entry Service also
hides its other Operations unless they are separately declared entries.

Filtering does not change source frames, JSON artifacts, metrics or the full-window diameter scale.
The node count describes the displayed subset; the metric detail table remains explicitly full-window.
The All button restores every Service selection. Copy exports the current filtered graph.

### Importance 过滤

Operation graph nodes may declare `properties.importance` as `high`, `medium`, or `low`.
Perf defaults unspecified Operations to `medium` and normalizes entry Operations to `high` in the
derived snapshot and report. Importance is project-owned attention priority, independent of QPS,
latency, error rate and any bottleneck judgment. Invalid importance values fail report rendering.

The Importance multi-select sits alongside Service before Fit. Both start fully selected and
persist across windows. A non-entry Operation must match both selected Service and Importance;
then directed entry reachability is applied. A high descendant behind an excluded low intermediate
is hidden too, unless another allowed path reaches it. No intermediate context is retained.
Entries remain pinned even if high is unchecked. Importance is shown in node details/hover;
metric values and node size scaling are unaffected. Generic GraphViews opt in via importance_filter.

### 数值过滤

The QPS multi-select has four groups: `= 0`, `N/A`, `< entry`, and `>= entry`.
QPS is nonnegative; classify zero separately before relative comparisons so the groups do not
overlap. All start selected. It intersects Service and Importance before
entry reachability pruning. Entries stay pinned, and the reference is always the original selected
window's entry value, never recalculated after filtering. Changing windows updates the reference
while preserving selections. A zero entry value is valid: every positive value is in `>= entry`.

Common uses GraphNode.comparison_value and GraphView.comparison_filter_label; Perf supplies QPS
numeric observations, independently of formatted labels. Missing/invalid values enter N/A.
If the entry value is missing or there is not exactly one entry, relative choices are disabled,
positive nodes bypass the relative test, and a visible explanation is shown. Zero and N/A filters
still apply. Multiple entry rates are never implicitly summed or chosen arbitrarily.
