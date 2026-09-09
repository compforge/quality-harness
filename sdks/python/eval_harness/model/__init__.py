"""Data model: the contracts every layer reads/writes.

- ``sample`` — ``Sample`` (read-only view a metric scores) + ``MetricResult``
  (dual channel: quality score vs raw measurement).
- ``evalset`` — ``EvalSet`` / ``SourceRecord`` / ``FacetSchema`` + ``eval_view`` (eval's read
  of a canonical ``common.Case``); cases themselves are the neutral ``common.Case`` (input side).
- ``experiment`` — ``Experiment`` / ``Arm`` / ``Service`` (the orchestration spec).
"""
