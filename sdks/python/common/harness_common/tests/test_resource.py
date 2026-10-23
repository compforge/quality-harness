"""Cross-language resource identity and inventory coverage contracts."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from harness_common import (
    Resource,
    ResourceCoverage,
    ResourceInstance,
    ResourceInstanceRef,
    ResourceMeasurement,
    ResourceSnapshot,
)

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[5] / "conformance/resources.json").read_text()
)


def test_resource_identity_does_not_change_with_display_or_service_association():
    resource = Resource(**FIXTURE["resource"])
    assert replace(resource, name="Renamed") == resource
    refs = [ResourceInstanceRef(**item) for item in FIXTURE["instances"]]
    assert refs[0] == refs[1]
    assert refs[0] != refs[2]
    assert refs[0] != refs[3]
    assert ResourceInstance(refs[0], state="retained") == ResourceInstance(
        refs[1], state="active"
    )


def test_partial_count_and_unavailable_are_distinct_from_empty():
    raw = FIXTURE["snapshots"][0]
    snapshot = ResourceSnapshot(
        resource=Resource(**FIXTURE["resource"]),
        environment=raw["environment"],
        observed_at=raw["observedAt"],
        source=raw["source"],
        scope=raw["scope"],
        coverage=ResourceCoverage(**raw["coverage"]),
        total_count=raw["totalCount"],
        measurements=tuple(ResourceMeasurement(**m) for m in raw["measurements"]),
        instances=(ResourceInstance(ResourceInstanceRef(**FIXTURE["instances"][0])),),
        next_cursor=raw["nextCursor"],
    )
    assert len(snapshot.instances) == 1
    assert snapshot.total_count == 17
    assert snapshot.measurements[0].scope == "observed"
    missing = replace(
        snapshot,
        coverage=ResourceCoverage("unavailable", "permission denied"),
        total_count=None,
        measurements=None,
        instances=None,
        next_cursor=None,
    )
    assert missing.total_count is None
    with pytest.raises(ValueError, match="requires a reason"):
        ResourceCoverage("partial")
