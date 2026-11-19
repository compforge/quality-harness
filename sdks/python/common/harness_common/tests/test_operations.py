import json
from pathlib import Path
from dataclasses import replace

import pytest
from harness_common import (
    Operation,
    Workload,
    Service,
    Component,
    Repository,
    Forge,
    Environment,
    ObservationDefinition,
    MetricQuery,
    graph_from_services,
    select_observations,
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
    probe = ObservationDefinition(
        "metricdb",
        {"qps": MetricQuery("counter", "rps")},
        kind="prometheus",
    )
    operation = Operation("read", service_name="api", observes=(probe,))
    workload = Workload("worker", observes=(probe,))
    service = Service("api", Component(repository=Repository(Forge("test"), "test/api"), name="api"), Environment("test"), workloads=(workload,), observes=(probe,))
    assert service.observes[0] is workload.observes[0] is operation.observes[0]
    assert service.id() == replace(service, observes=()).id()
    assert select_observations(operation.observes, "prometheus") == (probe,)
    with pytest.raises(ValueError, match="exactly one"):
        select_observations((probe,), "victoriametrics")
    with pytest.raises(ValueError, match="exactly one"):
        select_observations((probe, probe), "prometheus")


def test_universal_probe_and_invalid_backend_kind():
    probe = ObservationDefinition("metricdb", {"qps": MetricQuery("counter", "rps")})
    assert select_observations((probe,), "victoriametrics") == (probe,)
    for kind in ("", " "):
        with pytest.raises(ValueError, match="kind must be a nonempty string"):
            select_observations((replace(probe, kind=kind),), "prometheus")
