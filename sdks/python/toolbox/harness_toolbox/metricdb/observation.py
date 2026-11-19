"""Resolve Common observations for a MetricDB reader before issuing queries."""

import json
import re
from collections.abc import Mapping
from dataclasses import replace

from harness_common import ObservationDefinition, select_observations


def bind_metricdb_observations(
    declarations: tuple[ObservationDefinition, ...],
    backend: str,
    parameters: Mapping[str, str],
) -> tuple[ObservationDefinition, ...]:
    """@spec Scope parameters are label string values. The caller binds the time window."""

    def substitute(match: re.Match[str]) -> str:
        key = match[1]
        value = parameters.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"Missing metric scope parameter: {key}")
        return json.dumps(value, ensure_ascii=False)

    selected = select_observations(tuple(d for d in declarations if d.type == "metricdb"), backend)
    bound = []
    for declaration in selected:
        metrics = {}
        for name, metric in declaration.metrics.items():
            expression = re.sub(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}", substitute, metric.expression)
            if "{{" in expression or "}}" in expression:
                raise ValueError(f"Invalid metric scope placeholder in {name}")
            metrics[name] = replace(metric, expression=expression)
        bound.append(replace(declaration, metrics=metrics))
    return tuple(bound)
