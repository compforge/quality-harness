"""Service-held resource declarations and read-only inventory evidence, without discovery I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, Literal, TypeVar

from harness_common.service import Service

Details = TypeVar("Details")


@dataclass(frozen=True, slots=True)
class Resource:
    """A resource collection; its ID is shared across managing and consuming Services.

    Declarations do not prove live existence. Names and descriptions are not identity.
    """

    id: str
    kind: str = field(compare=False)
    name: str = field(compare=False)
    description: str | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class ServiceResource:
    """An association, not exclusive ownership or a new copy of the resource."""

    service: Service
    resource: Resource
    relationship: Literal["manages", "uses"]


@dataclass(frozen=True, slots=True)
class ResourceInstanceRef:
    """Environment and resource collection IDs disambiguate local instance IDs."""

    environment: str
    resource: str
    id: str


@dataclass(frozen=True, slots=True)
class ResourceInstance(Generic[Details]):
    ref: ResourceInstanceRef
    name: str | None = field(default=None, compare=False)
    state: str | None = field(default=None, compare=False)
    details: Details | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class ResourceMeasurement:
    """A finite quantity with an explicit unit and observed-subset or total scope."""

    name: str
    value: float
    unit: str
    scope: Literal["observed", "total"]


@dataclass(frozen=True, slots=True)
class ResourceCoverage:
    status: Literal["complete", "partial", "unavailable"]
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.status not in ("complete", "partial", "unavailable"):
            raise ValueError("Invalid resource coverage status")
        if self.status != "complete" and not self.reason:
            raise ValueError("Incomplete resource coverage requires a reason")


@dataclass(frozen=True, slots=True)
class ResourceSnapshot(Generic[Details]):
    """Read-only evidence; consumers own access, collection and presentation.

    @spec total_count is independently known for scope, never inferred from a page.
    @spec Missing is unknown; incomplete acquisition is not a resource health verdict.
    @rule source, scope and details contain serializable evidence, never credentials.
    """

    resource: Resource
    environment: str
    observed_at: str
    source: str
    scope: dict[str, str]
    coverage: ResourceCoverage
    total_count: int | None = None
    measurements: tuple[ResourceMeasurement, ...] | None = None
    instances: tuple[ResourceInstance[Details], ...] | None = None
    next_cursor: str | None = None
