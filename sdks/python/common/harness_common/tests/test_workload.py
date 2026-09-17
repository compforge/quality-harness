from dataclasses import replace
import json
from pathlib import Path

import pytest
from harness_common import (
    Component,
    Environment,
    Forge,
    KubernetesWorkload,
    KubernetesWorkloadInstance,
    Repository,
    Service,
    Workload,
)

COMPONENT = Component(Repository(Forge("git"), "org/repo"), "api")
FIXTURE = json.loads(
    (Path(__file__).resolve().parents[5] / "conformance/workloads.json").read_text()
)


def declaration(value):
    return KubernetesWorkload(
        **{key: item for key, item in value.items() if key != "platform"}
    )


def instance(value):
    return KubernetesWorkloadInstance(
        **{key: item for key, item in value.items() if key != "platform"}
    )


def test_workload_mapping_does_not_change_logical_service_identity():
    service = Service("business", COMPONENT, Environment("test"))
    api, worker = map(declaration, FIXTURE["workloads"][:2])
    deployed = replace(service, workloads=(api, worker))
    assert deployed.workloads == (api, worker)
    assert service.workloads == ()
    assert deployed == service and hash(deployed) == hash(service)
    assert replace(service, name="another") != service


def test_workload_declaration_can_be_shared_by_services():
    workload = Workload("external-process")
    first = Service("first", COMPONENT, Environment("test"), workloads=(workload,))
    assert replace(first, name="second").workloads[0] is first.workloads[0]


def test_workload_location_and_container_have_distinct_roles():
    api = declaration(FIXTURE["workloads"][0])
    assert api.description == "Agent execution runtime"
    assert replace(api, description="Another explanation") == api
    assert declaration(FIXTURE["workloads"][1]).description is None
    assert replace(api, namespace="other") != api
    assert (
        replace(api, location={**api.location, "resource_kind": "StatefulSet"}) != api
    )
    assert replace(api, container="sidecar") == api


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": ""},
        {"namespace": ""},
        {"location": {"kind": "resource", "resource_kind": "Service", "name": "api"}},
        {"location": {"kind": "labels", "labels": {}}},
    ],
)
def test_workload_requires_location_and_excludes_network_service(kwargs):
    with pytest.raises(ValueError):
        replace(declaration(FIXTURE["workloads"][0]), **kwargs)


def test_shared_location_variants_and_replica_relationship():
    workloads = list(map(declaration, FIXTURE["workloads"]))
    assert [workload.location["kind"] for workload in workloads] == [
        "resource",
        "service",
        "labels",
    ]
    assert workloads[0].name == "agent"
    assert workloads[0].location["name"] == "hibot-agent"
    assert workloads[2].namespace is None
    assert [item.workload for item in map(instance, FIXTURE["instances"])] == [
        "agent",
        "agent",
    ]


def test_shared_instance_identity():
    first, second = map(instance, FIXTURE["instances"])
    assert first != second
    for change in FIXTURE["identity_changes"]:
        candidate = replace(first, **change["patch"])
        assert (first == candidate) is change["same"]
        if change["same"]:
            assert hash(first) == hash(candidate)
