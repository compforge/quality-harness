import json
from pathlib import Path
from dataclasses import replace

import pytest
from harness_common import (
    Operation,
    Service,
    Component,
    Repository,
    Forge,
    Environment,
    ProbeDefinition,
    MetricQuery,
    graph_from_services,
    select_probes,
)


def test_shared_operation_declarations():
    root = Path(__file__).resolve().parents[5]
    fixture = json.loads((root / "conformance/common/operations.json").read_text())
    services = []
    for item in fixture["operations"]:
        operation = Operation(
            item["name"],
            service_name=item["service"],
            calls=tuple(item["calls"]),
            may_calls=tuple(item["mayCalls"]),
        )
        name = item["service"]
        services.append(
            Service(
                name=name,
                environment=Environment("test"),
                component=Component(
                    repository=Repository(Forge("test"), "fixture/app"), name=name
                ),
                operations=(operation,),
            )
        )
    graph = graph_from_services(services)
    assert len(graph.nodes) == 4
    assert len(graph.relations) == 4
    services[0] = replace(
        services[0],
        operations=(Operation("chat", service_name="front", calls=("missing",)),),
    )
    with pytest.raises(ValueError):
        graph_from_services(services)


def test_probe_backend_selection():
    probe = ProbeDefinition(
        "prometheus",
        {"qps": MetricQuery("counter", "rps")},
        datasource_kind="prometheus",
    )
    assert select_probes((probe,), "prometheus") == (probe,)
    with pytest.raises(ValueError, match="exactly one"):
        select_probes((probe,), "victoriametrics")
    with pytest.raises(ValueError, match="exactly one"):
        select_probes((probe, probe), "prometheus")
