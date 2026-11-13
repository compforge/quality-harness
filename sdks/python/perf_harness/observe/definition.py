"""Project observation declarations composed from Common targets and perf producers."""

from dataclasses import dataclass

from harness_common import Operation, Workload

from perf_harness.metric import parse_ref, validate_ref
from perf_harness.model import ObservationBinding, Service
from perf_harness.observe.base import Probe


@dataclass(frozen=True)
class ObservationSpec:
    """What to observe and which producers supply it; not an observed fact.

    @rule Workload identity is its name within the owning Common Service.
    @link component://sdks/python/perf_harness/docs/observation.md
    """

    service: Service
    probes: tuple[Probe, ...]
    operation: Operation | None = None
    workload: Workload | None = None

    def __post_init__(self) -> None:
        if self.operation is not None and self.workload is not None:
            raise ValueError("observation selects an operation or a workload, not both")
        if self.operation and self.operation.service_name != self.service.name:
            raise ValueError("observation operation belongs to a different service")
        if self.workload and self.workload not in self.service.workloads:
            raise ValueError("observation workload is not declared by its service")
        if not self.probes:
            raise ValueError("observation must declare at least one producer")
        for probe in self.probes:
            if probe.labels.get("service") != self.service.name:
                raise ValueError("probe service differs from observation target")
            if probe.labels.get("operation") != (self.operation.id() if self.operation else None):
                raise ValueError("probe operation differs from observation target")
            if probe.labels.get("workload") != (self.workload.name if self.workload else None):
                raise ValueError("probe workload differs from observation target")
            binding = probe.observation_binding
            if binding and (
                binding.target != self.target
                or binding.workload != (self.workload.name if self.workload else None)
            ):
                raise ValueError("probe metric binding differs from observation target")

    @property
    def target(self) -> str:
        return self.operation.id() if self.operation else self.service.id()


def collect_bindings(probes: list[Probe]) -> list[ObservationBinding]:
    """Validate output addresses and merge disjoint attributes before any I/O."""
    from dataclasses import replace

    bindings: dict[tuple[str, str | None], ObservationBinding] = {}
    for probe in probes:
        binding = probe.observation_binding
        if binding is None:
            continue
        expected = probe.labels.get("operation") or Service(binding.service).id()
        if (
            binding.target != expected
            or probe.labels.get("service") != binding.service
            or binding.workload != probe.labels.get("workload")
        ):
            raise ValueError("observation binding differs from producer target")
        registry = {family.name: family for family in probe.describe()}
        for name, metric in binding.metrics.items():
            validate_ref(metric.metric, registry)
            family, labels, _ = parse_ref(metric.metric)
            if registry[family].unit != metric.unit:
                raise ValueError(f"observation unit differs from producer for {name}")
            if labels != probe.labels:
                raise ValueError(f"observation metric must select its target: {name}")
        key = (binding.target, binding.workload)
        if previous := bindings.get(key):
            if previous.metrics.keys() & binding.metrics.keys():
                raise ValueError(f"duplicate observation metric for {binding.target}")
            if replace(previous, metrics=binding.metrics) != binding:
                raise ValueError(f"inconsistent observation target {binding.target}")
            binding = replace(previous, metrics=previous.metrics | binding.metrics)
        bindings[key] = binding
    return list(bindings.values())
