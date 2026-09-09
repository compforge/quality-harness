"""view —— 呈现层：把 model(+findings) 排版成各种视图。判读与展示解耦，读 Finding 上色。

- callstack：markdown tree，一次性多分辨率剪枝（一屏看全貌）
- explore + state：有状态探索（缩略图 + expand/focus，逐步展开大树）
- text / interactive / series：框线树 / 交互页(唯一 HTML，静态用 md) / 时序 sparkline
"""

from __future__ import annotations

from trace_harness.view.callstack import findings_block as findings_block
from trace_harness.view.engine import render_callstack as render_callstack  # 唯一渲染路径（facet）
from trace_harness.view.engine import render_md as render_md  # facet-engine 版（折 http/代理链）
from trace_harness.view.explore import render_explore as render_explore
from trace_harness.view.interactive import render_interactive as render_interactive
from trace_harness.view.series import render_series as render_series
from trace_harness.view.state import ViewState as ViewState
from trace_harness.view.text import render_text as render_text
