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
