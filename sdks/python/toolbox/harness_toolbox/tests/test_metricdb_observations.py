"""Backend selection and label binding use the shared measurement declaration."""

import pytest
from harness_common import ObservationDefinition

from harness_toolbox.metricdb import bind_metricdb_observations


def test_label_values_are_quoted_without_mutating_the_reusable_expression():
    expr = "sum(rate(c{tenant={{tenant}}}[$window]))"
    definition = ObservationDefinition.from_dict(
        {
            "type": "metricdb",
            "metrics": {"completed": {"expression": expr, "unit": "rps"}},
        }
    )
    (bound,) = bind_metricdb_observations((definition,), "prometheus", {"tenant": 'a"b'})
    assert bound.metrics["completed"].expression == r'sum(rate(c{tenant="a\"b"}[$window]))'
    assert definition.metrics["completed"].expression == expr
    with pytest.raises(ValueError, match="Missing metric scope parameter: tenant"):
        bind_metricdb_observations((definition,), "prometheus", {})
