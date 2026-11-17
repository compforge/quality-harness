# Report Kit browser components

Repository-owned browser code shared by Python and TypeScript offline reports. This is a build
source, not an independently published package. Domain adapters prepare graph frames and own the
report shell; the component owns graph DOM, styles, layout, filtering and interaction.

- `graph/view.js`: `rkGraph(root, view)` mounts controls in a `.graph-view` host, preserving optional
  pre-rendered `[data-graph-frame]` table panels. Titles and labels are assigned as text.
- `graph/layout.js`, `graph/filter.js`: neutral placement and reachability algorithms.
- `graph/style.css`: component-scoped presentation; an ancestor's `data-wide="true"` enables viewport sizing.
- `vendor/`: pinned offline distributions and their license notices; provenance is recorded there.
- `tests/`: browser algorithm checks, run with `make test` (Node required); `make lint` checks syntax.

The view payload contains `title`, `frames`, `group_filter_label`, `importance_filter` and
`comparison_filter_label`. A frame supplies `label`, `group`, `nodes`, `edges` and `entry_ids`.
Each node supplies its `id`, display `label`, optional `group`, `importance`, `diameter`, `color`,
`details`, `tooltip` and `comparison_value`; edges supply `source`, `target`, `label` and `dashed`.
The component consumes prepared values; it never queries services or calculates domain metrics.

Python's `harness-common` build hook copies `graph/` and `vendor/` into packaged resources for
wheels, sdists and editable installs. `make test` in `sdks/python` refreshes the generated copy.
TypeScript perf embeds these same sources at build time. Neither distributed SDK needs the
checkout, Python/Node of the other SDK, or a CDN at report generation/viewing time.
