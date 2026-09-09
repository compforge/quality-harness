"""Report layer: pure pivot over the Worksheet.

Reads only the Worksheet (+ resolved weights + facet schema) and renders views.
It never knows how a cell was produced — the table is the contract. Because it
is pure, it can be re-run any time (re-report after rescore, cross-experiment,
multiple formats) without touching the system under test.

Hard rule: a missing (arm_id × case) cell renders ``—`` and is excluded from
aggregates — never imputed as 0 — so a half-crashed arm_id reads as *incomplete*,
not *worse*. Coverage (``n_scored`` / ``n_cases``) is always surfaced.
"""
