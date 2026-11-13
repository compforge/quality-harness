# Report Graph layout

Common report_kit renders already reduced GraphView data using offline Cytoscape and bundled
ELK 0.12.1. It does not query services or calculate domain metrics. Rendering keeps every node,
edge, label, group, detail and QPS-derived size supplied by the domain view.

Placement has two levels. Inside each named group, independent nodes use ELK rectangle packing
so parallel APIs occupy multiple columns. If the group has internal edges, it uses directed layered
layout instead. Ungrouped nodes remain individual boxes. The outer layered layout arranges these
boxes according to inter-group links; these temporary links are only positioning constraints.
The displayed graph retains the original Operation edges, including conditional edges and cycles.

The renderer compares four candidates: two local packing aspect ratios derived from the actual
viewport, each with outer RIGHT and DOWN flow. It chooses the candidate maximizing the scale
that fits the viewport. It never changes node dimensions to stretch the drawing or imposes a fixed
pixel width/height on the graph. Service grouping remains auxiliary; it does not replace Operations.

Only visible viewports are laid out. Frame changes and actual viewport resizes recompute positions;
manual pan/zoom keeps its view until that happens. Generation checks prevent stale asynchronous
results from overwriting a newly selected frame. Loading/failure state is visible, and image copying
is disabled while positions are pending. Full-image copy still exports the complete graph.

Call / may_call retain their arrow and solid/dashed styles; repetitive edge text is shown on hover
or selection. Other edge labels remain visible. Layout changes do not alter JSON graph facts or
metric identities, and do not prove execution of a static path.

The ELK bundle runs in-process without a remote worker, CDN or runtime package install. Its pinned
source, checksum and license are recorded in the assets README. This replaces the prior Dagre bundle.

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
