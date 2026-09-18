"""Resolve metric scrape targets through the existing Workload resource backend."""

from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from harness_common import KubernetesWorkload
from harness_common.client import _ClientBorrower
from prombed import ScrapeTarget

from harness_toolbox.environment import KubernetesEnvironment


@dataclass(frozen=True)
class KubernetesScrapeDiscovery:
    """Discover Pod incarnations each tick; Pod addresses must be reachable by the scraper."""

    environment: KubernetesEnvironment
    workloads: tuple[KubernetesWorkload, ...]
    port: int
    path: str = "/metrics"
    scheme: str = "http"

    def __post_init__(self) -> None:
        if not self.workloads or not 0 < self.port < 65536:
            raise ValueError("Metrics discovery needs workloads and a valid port")
        if (
            self.scheme not in {"http", "https"}
            or not self.path.startswith("/")
            or urlsplit(self.path).netloc
        ):
            raise ValueError("Invalid metrics discovery scheme or path")

    async def resolve(self, clients: _ClientBorrower) -> list[ScrapeTarget]:
        targets: dict[str, ScrapeTarget] = {}
        for workload in self.workloads:
            environment = replace(
                self.environment,
                options=replace(
                    self.environment.options,
                    namespace=workload.namespace or self.environment.options.namespace,
                ),
            )
            resources = await clients.get(environment)
            instances = await resources.resolve_workload(workload, environment=environment.name)
            for instance in instances:
                pod = await resources.get("v1", "Pod", instance.pod)
                if pod["metadata"]["uid"] != instance.uid:
                    raise ValueError("Metrics target changed during discovery")
                status = pod.get("status", {})
                address = status.get("podIP")
                if (
                    pod["metadata"].get("deletionTimestamp")
                    or status.get("phase") != "Running"
                    or not address
                ):
                    continue
                host = f"[{address}]" if ":" in address else address
                # Pod UID, not IP, separates replacement counters even when IPs are reused.
                identity = f"{instance.namespace}/{instance.uid}"
                targets[identity] = ScrapeTarget(
                    f"{self.scheme}://{host}:{self.port}{self.path}",
                    labels={"instance": identity},
                )
        return [targets[key] for key in sorted(targets)]
