from dataclasses import replace

import pytest
from harness_common import (
    Component,
    Environment,
    Forge,
    KubernetesWorkload,
    Repository,
    Service,
    Workload,
)

COMPONENT = Component(Repository(Forge("git"), "org/repo"), "api")


def test_workload_mapping_does_not_change_logical_service_identity():
    service = Service("business", COMPONENT, Environment("test"))
    api = KubernetesWorkload("physical-api", "api-ns")
    worker = KubernetesWorkload("physical-worker", "worker-ns", kind="StatefulSet")
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
    api = KubernetesWorkload("api", "namespace", container="business")
    assert replace(api, namespace="other") != api
    assert replace(api, kind="StatefulSet") != api
    assert replace(api, container="sidecar") == api


@pytest.mark.parametrize(
    "kwargs", [{"name": ""}, {"namespace": ""}, {"kind": "Service"}]
)
def test_workload_requires_location_and_excludes_network_service(kwargs):
    with pytest.raises(ValueError):
        KubernetesWorkload(**{"name": "api", "namespace": "ns", **kwargs})
