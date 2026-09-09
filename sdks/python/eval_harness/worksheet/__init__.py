"""The Worksheet: the in-memory big table that is the engine's single truth.

One row per (arm_id × case); columns = identity + dimensions + seed (query /
ground_truth) + solve outputs (response / retrieved / citations / raw
observations) + per-metric score cells + provenance. The reconciler drives the
table toward "all cells filled"; the report reads it by pure pivot. Cells carry
state so resume = reload + fill whatever is still PENDING/FAILED.
"""
