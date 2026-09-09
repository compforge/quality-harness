"""K8s-family probes — the common Source today, but only one Source family.

These read the Service's pod via ``kubectl`` (top / exec ps / restartCount).
The parsers are pure functions so they are unit-tested without a cluster; the
probes that wrap them simply shell out and skip themselves when the Service has
no Kubernetes coordinates.
"""

from __future__ import annotations

import json

from perf_harness.metric import series_id
from perf_harness.model import Service
from perf_harness.observe.base import FamilySpec, Probe, ProbeContext
from perf_harness.sh import run_capture


async def _pod_name(service: Service) -> str | None:
    out = await run_capture(
        [
            "kubectl",
            "--kubeconfig",
            service.environment.kubeconfig,
            "-n",
            service.namespace,
            "get",
            "pod",
            "-l",
            service.k8s_selector,
            "-o",
            "jsonpath={.items[0].metadata.name}",
        ]
    )
    return out.strip() or None


class _K8sProbe(Probe):
    """Base for kubectl-family probes — observe the Service's pod by default, or a
    specific Service's pod when explicitly bound to it.

    ``observe:`` in the config builds these against downstream services on the
    request chain (e.g. api / worker), so one run can chart cpu/mem across the
    whole chain, not just the Service. A bound probe prefixes its metric name with
    the service (``top`` → ``top.worker``) — series never collide, and the report
    overlays same-unit series (api cpu vs worker cpu) on one chart.
    """

    source = "k8s"

    def __init__(self, *, target_service: Service | None = None, per_pod: bool = False) -> None:
        self._target_service = target_service
        self._service = target_service.name if target_service else None
        # per_pod fans the service-level reading out into one series per pod: sample()
        # keys carry a {pod} label (series_id form) on top of the probe's {service}, and
        # everything downstream (engine grouping, charts, pivot) treats pod as just
        # another label. Only top/limits honor it; off → the sum-over-pods (unchanged).
        self._per_pod = per_pod
        if target_service:  # unique store id; family stays "top", service is a label
            self.name = f"{self.name}.{target_service.name}"

    def _ref(self, ctx: ProbeContext) -> Service | None:
        """The explicitly bound Service, else the experiment Service."""
        service = self._target_service or ctx.service
        if not (service.environment.kubeconfig and service.namespace and service.k8s_selector):
            return None
        return service


class KubectlTopProbe(_K8sProbe):
    """Pod cpu/memory as the scheduler sees it (``kubectl top pod``)."""

    name = "top"
    families = {
        "cpu_m": FamilySpec(
            "millicores", "gauge", "pod CPU as the scheduler sees it (kubectl top)", ("pod",)
        ),
        "mem_mi": FamilySpec("MiB", "gauge", "pod working-set memory (kubectl top)", ("pod",)),
    }

    async def sample(self, ctx: ProbeContext) -> dict[str, float]:
        service = self._ref(ctx)
        if not service:
            return {}
        # select ALL pods of the service (-l label), not just items[0]: by default the
        # reading is the per-tick SUM across replicas (gauge summary = peak-of-sum,
        # "did total cpu/mem spike"); per_pod keeps the rows separate instead — one
        # {pod}-labeled series per replica ("which pod is hot / near ITS limit").
        text = await run_capture(
            [
                "kubectl",
                "--kubeconfig",
                service.environment.kubeconfig,
                "-n",
                service.namespace,
                "top",
                "pod",
                "-l",
                service.k8s_selector,
                "--no-headers",
            ]
        )
        if self._per_pod:
            out: dict[str, float] = {}
            for pod, (cpu_m, mem_mi) in parse_kubectl_top_per_pod(text).items():
                if cpu_m is not None:
                    out[series_id("cpu_m", {"pod": pod})] = cpu_m
                if mem_mi is not None:
                    out[series_id("mem_mi", {"pod": pod})] = mem_mi
            return out
        cpu_m, mem_mi = parse_kubectl_top(text)
        out = {}
        if cpu_m is not None:
            out["cpu_m"] = cpu_m
        if mem_mi is not None:
            out["mem_mi"] = mem_mi
        return out


class PerWorkerRSSProbe(_K8sProbe):
    """Per-uvicorn-process RSS via ``kubectl exec ps`` — the worker × baseline view."""

    name = "rss"
    families = {
        "rss_total_mi": FamilySpec(
            "MiB", "gauge", "total RSS across uvicorn master + worker processes"
        ),
        "n_workers": FamilySpec("count", "gauge", "uvicorn worker process count"),
    }

    async def sample(self, ctx: ProbeContext) -> dict[str, float]:
        service = self._ref(ctx)
        if not service:
            return {}
        pod = await _pod_name(service)
        if not pod:
            return {}
        cmd = [
            "kubectl",
            "--kubeconfig",
            service.environment.kubeconfig,
            "-n",
            service.namespace,
            "exec",
            pod,
        ]
        if service.container:
            cmd += ["-c", service.container]
        cmd += ["--", "ps", "-eo", "pid,rss,args"]
        text = await run_capture(cmd)
        n_workers, rss_total_mi = parse_ps_rss(text)
        return {"rss_total_mi": rss_total_mi, "n_workers": float(n_workers)}


class RestartProbe(_K8sProbe):
    """Container restart count — a cheap proxy for OOMKilled / crash under load."""

    name = "restart"
    families = {
        # a counter: only ever climbs; total = run total
        "restarts": FamilySpec(
            "count", "counter", "container restart count (OOMKilled / crash proxy)"
        ),
    }

    async def sample(self, ctx: ProbeContext) -> dict[str, float]:
        service = self._ref(ctx)
        if not service:
            return {}
        out = await run_capture(
            [
                "kubectl",
                "--kubeconfig",
                service.environment.kubeconfig,
                "-n",
                service.namespace,
                "get",
                "pod",
                "-l",
                service.k8s_selector,
                "-o",
                "jsonpath={.items[*].status.containerStatuses[*].restartCount}",
            ]
        )
        total = sum(int(x) for x in out.split() if x.isdigit())
        return {"restarts": float(total)}


class PodCountProbe(_K8sProbe):
    """Selected pod counts by lifecycle state.

    Unlike resource usage, replica count is itself a capacity signal for
    autoscaled services. One ``pods.count{state=…}`` family keeps the state
    vocabulary as bounded labels so arbitrary services can use the same probe.
    """

    name = "pods"
    families = {
        "count": FamilySpec(
            "count", "gauge", "selected Kubernetes pods by lifecycle state", ("state",)
        ),
    }

    async def sample(self, ctx: ProbeContext) -> dict[str, float]:
        service = self._ref(ctx)
        if not service:
            return {}
        text = await run_capture(
            [
                "kubectl",
                "--kubeconfig",
                service.environment.kubeconfig,
                "-n",
                service.namespace,
                "get",
                "pod",
                "-l",
                service.k8s_selector,
                "-o",
                "json",
            ]
        )
        return {
            series_id("count", {"state": state}): float(value)
            for state, value in parse_pod_counts(text).items()
        }


class ResourceLimitsProbe(_K8sProbe):
    """The container's configured resource **request/limit** (from the pod spec) — the
    allotment, vs ``top``'s usage. The probe re-reads the selected pod set each
    tick because replicas may change during a trial; caching the initial set
    would make autoscaling curves report stale aggregate limits.

    Despite the name it emits BOTH ``request`` and ``limit`` (k8s calls the block
    ``ResourceRequirements``). Same units as ``top`` (cpu millicores / mem MiB), so the
    report overlays them on the same cpu/mem chart as flat reference lines ('using vs
    limit') with NO special rendering — a constant is just a metric that doesn't move.

    By default summed across ALL pods × ALL containers (matching ``top.cpu_m{service}``'s
    sum-over-pods peak-of-sum), so under replicas>1 the line is the service-level total;
    ``per_pod`` keeps one {pod}-labeled series per pod instead — each pod's own
    allotment, pairing with per_pod ``top`` usage on the same chart."""

    name = "limits"
    # units MUST match KubectlTopProbe (millicores / MiB) so the report overlays
    # these reference lines on the same usage chart (it groups series by unit).
    families = {
        "cpu_request": FamilySpec(
            "millicores",
            "gauge",
            "container CPU request (pod spec, summed over containers)",
            ("pod",),
        ),
        "cpu_limit": FamilySpec(
            "millicores",
            "gauge",
            "container CPU limit (pod spec, summed over containers)",
            ("pod",),
        ),
        "mem_request": FamilySpec(
            "MiB",
            "gauge",
            "container memory request (pod spec, summed over containers)",
            ("pod",),
        ),
        "mem_limit": FamilySpec(
            "MiB",
            "gauge",
            "container memory limit (pod spec, summed over containers)",
            ("pod",),
        ),
    }

    async def sample(self, ctx: ProbeContext) -> dict[str, float]:
        service = self._ref(ctx)
        if not service:
            return {}
        # one `get pod -o json` read; the per-pod parse is the single source — the
        # service-level reading is just its sum, so the two views can never disagree
        text = await run_capture(
            [
                "kubectl",
                "--kubeconfig",
                service.environment.kubeconfig,
                "-n",
                service.namespace,
                "get",
                "pod",
                "-l",
                service.k8s_selector,
                "-o",
                "json",
            ]
        )
        per_pod = parse_pod_resources(text)
        out: dict[str, float] = {}
        if self._per_pod:
            for pod, vals in per_pod.items():
                for metric, v in vals.items():
                    out[series_id(metric, {"pod": pod})] = v
        else:
            for vals in per_pod.values():
                for metric, v in vals.items():
                    out[metric] = out.get(metric, 0.0) + v
        return out


def parse_pod_counts(text: str) -> dict[str, int]:
    """Parse ``kubectl get pod -o json`` into bounded lifecycle counts."""
    counts = {
        "total": 0,
        "active": 0,
        "ready": 0,
        "running": 0,
        "pending": 0,
        "unschedulable": 0,
        "terminating": 0,
    }
    for pod in json.loads(text or "{}").get("items", []):
        counts["total"] += 1
        metadata = pod.get("metadata") or {}
        status = pod.get("status") or {}
        phase = str(status.get("phase") or "").lower()
        if phase not in ("succeeded", "failed"):
            counts["active"] += 1
        if phase in ("running", "pending"):
            counts[phase] += 1
        if metadata.get("deletionTimestamp"):
            counts["terminating"] += 1
        conditions = status.get("conditions") or []
        if not metadata.get("deletionTimestamp") and any(
            c.get("type") == "Ready" and c.get("status") == "True" for c in conditions
        ):
            counts["ready"] += 1
        if any(
            c.get("type") == "PodScheduled"
            and c.get("status") == "False"
            and c.get("reason") == "Unschedulable"
            for c in conditions
        ):
            counts["unschedulable"] += 1
    return counts


# ---------------------------------------------------------------------------
# Pure parsers (unit-tested without a cluster)
# ---------------------------------------------------------------------------


def parse_kubectl_top_per_pod(text: str) -> dict[str, tuple[float | None, float | None]]:
    """``POD 123m 456Mi`` rows (``--no-headers``) → ``{pod: (cpu_millicores, mem_MiB)}``,
    one entry per pod. A header / unparseable row is skipped. The single row-parse
    source — the summed view (``parse_kubectl_top``) derives from it."""
    out: dict[str, tuple[float | None, float | None]] = {}
    for raw in text.splitlines():
        cols = raw.split()
        if len(cols) < 3:
            continue
        cpu = _num(cols[-2].removesuffix("m"))
        mem = _num(cols[-1].removesuffix("Mi"))
        if cpu is None and mem is None:
            continue  # header / unparseable row
        out[cols[0]] = (cpu, mem)
    return out


def parse_kubectl_top(text: str) -> tuple[float | None, float | None]:
    """Per-pod rows summed → (Σcpu_millicores, Σmem_MiB), the service-level total
    (peak-of-sum once summarized), correct under replicas>1 where one pod misses the
    rest. ``(None, None)`` if no row parsed."""
    rows = parse_kubectl_top_per_pod(text).values()
    if not rows:
        return (None, None)
    return (
        sum(cpu or 0.0 for cpu, _ in rows),
        sum(mem or 0.0 for _, mem in rows),
    )


def parse_pod_resources(json_text: str) -> dict[str, dict[str, float]]:
    """``kubectl get pod -o json`` → per-pod request/limit, containers summed within
    each pod: ``{pod: {cpu_request/cpu_limit: millicores, mem_request/mem_limit: MiB}}``.
    A field absent on every container of a pod (no limit set) is omitted for that pod;
    unparseable JSON → ``{}`` (the probe skips itself this tick)."""
    try:
        items = json.loads(json_text).get("items", [])
    except (ValueError, AttributeError):
        return {}
    out: dict[str, dict[str, float]] = {}
    for item in items:
        pod = (item.get("metadata") or {}).get("name")
        if not pod:
            continue
        acc: dict[str, float] = {}
        for c in (item.get("spec") or {}).get("containers", []):
            res = c.get("resources") or {}
            for block, suffix in (("requests", "request"), ("limits", "limit")):
                vals = res.get(block) or {}
                cpu = _parse_cpu_m(vals.get("cpu", ""))
                if cpu is not None:
                    acc[f"cpu_{suffix}"] = acc.get(f"cpu_{suffix}", 0.0) + cpu
                mem = _parse_mem_mi(vals.get("memory", ""))
                if mem is not None:
                    acc[f"mem_{suffix}"] = acc.get(f"mem_{suffix}", 0.0) + mem
        if acc:
            out[pod] = acc
    return out


def parse_ps_rss(text: str) -> tuple[int, float]:
    """``pid rss args`` lines → (n_workers, total_rss_MiB) for uvicorn processes.

    Total RSS sums every uvicorn process (master + workers); worker count is the
    number of multiprocessing children, matching how ``--workers`` forks.
    """
    rss_kb_total = 0.0
    n_workers = 0
    for raw in text.splitlines():
        if "uvicorn" not in raw and "multiprocessing" not in raw:
            continue
        cols = raw.split(None, 2)
        if len(cols) < 3:
            continue
        rss = _num(cols[1])
        if rss is None:
            continue
        rss_kb_total += rss
        if "multiprocessing" in cols[2] or "spawn_main" in cols[2]:
            n_workers += 1
    return n_workers, rss_kb_total / 1024.0


def _num(s: str) -> float | None:
    try:
        return float(s)
    except ValueError:
        return None


def _parse_cpu_m(v: str) -> float | None:
    """A k8s cpu quantity → millicores: ``500m``→500, ``2``→2000, ``1.5``→1500."""
    v = v.strip()
    if not v:
        return None
    try:
        return float(v[:-1]) if v.endswith("m") else float(v) * 1000.0
    except ValueError:
        return None


# memory unit → MiB multiplier; longer suffixes first so "Mi" matches before "M"
_MEM_UNITS = (
    ("Ki", 1 / 1024),
    ("Mi", 1.0),
    ("Gi", 1024.0),
    ("Ti", 1024.0 * 1024),
    ("K", 1000 / 1024 / 1024),
    ("M", 1000**2 / 1024 / 1024),
    ("G", 1000**3 / 1024 / 1024),
    ("T", 1000**4 / 1024 / 1024),
)


def _parse_mem_mi(v: str) -> float | None:
    """A k8s memory quantity → MiB: ``2Gi``→2048, ``512Mi``→512, a plain int = bytes."""
    v = v.strip()
    if not v:
        return None
    for suf, mul in _MEM_UNITS:
        if v.endswith(suf):
            try:
                return float(v[: -len(suf)]) * mul
            except ValueError:
                return None
    try:
        return float(v) / (1024 * 1024)  # bare number = bytes → MiB
    except ValueError:
        return None
