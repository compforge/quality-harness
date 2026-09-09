"""analyze —— 判读包。

- 单 trace（node-scope）判读住 `diagnose/`：error / per-kind rules / 内置拓扑 detector
  （detached / obs_hole / propagated，走全局 detector 注册表）/ outlier / trend / pattern，
  逐 trace 产 node-scope Finding。
- gates 住 `verdict.py`。
- 跨 trace（cohort）判读住独立的 `corpus/` 包：吃三表产结构化 dict（当前不产 cohort-scope
  Finding——scope 字段在 Finding 上已留位，但 cohort 这条路暂未接通）。

消费方直接从子模块 import（`from trace_harness.analyze.diagnose import diagnose` 等）；本包不再
维护无人读的字符串清单。
"""

from __future__ import annotations
