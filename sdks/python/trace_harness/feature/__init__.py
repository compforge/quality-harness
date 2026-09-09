"""feature —— 特征层：从 node 算命名值。统一了 build(eager+raw) / derive(eager+facts) /
repro(lazy)，全是 `Feature` 在 (bake × 读raw) 平面上的点（见 feature.py）。

消费方通过 ``TraceContributions.features`` 显式组合，无模块级注册表。
"""

from __future__ import annotations

from trace_harness.feature.builtins import BUILTIN_FEATURES as BUILTIN_FEATURES
from trace_harness.feature.ctx import Ctx as Ctx
from trace_harness.feature.engine import bake_features as bake_features
from trace_harness.feature.engine import lazy_features as lazy_features
from trace_harness.feature.feature import Feature as Feature
from trace_harness.feature.registry import FeatureRegistry as FeatureRegistry
