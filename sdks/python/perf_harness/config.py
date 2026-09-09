"""Config — a YAML run file → an Experiment (+ runs dir).

Keeps the wiring (the service, which Probes, the resources × load grid)
declarative so a run is reproducible and reviewable. See ``examples/`` for a
filled-in file.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import get_args

import yaml
from harness_common import Component, Deployer, Forge, Repository
from harness_common.overlay import Overlay
from spec_case.facets import FacetSchema
from spec_case.model import Case, CaseSet, load_caseset, validate

from perf_harness.deploy import HelmDeployer
from perf_harness.drive.load import LoadModel, LoadProfile, Pacing, PacingKind, Schedule, Stage
from perf_harness.drive.workload import MockWorkload, Workload, build_workload
from perf_harness.engine import Experiment
from perf_harness.metric import MetricFamily, parse_ref, validate_ref
from perf_harness.metric.store import PER_REQUEST_DESCRIPTORS, REQUEST_DESCRIPTORS, SLO_METRICS
from perf_harness.model import (
    Deployment,
    Environment,
    ResourceProfile,
    Service,
    SloAssertion,
    SloOp,
    WindowKind,
    WindowSelector,
)
from perf_harness.observe import (
    ClientProbe,
    KubectlTopProbe,
    PerWorkerRSSProbe,
    PodCountProbe,
    Probe,
    ProbeConfig,
    PrometheusProbe,
    PrometheusQuery,
    ResourceLimitsProbe,
    RestartProbe,
    build_probe,
)

# valid enum values, derived from the Literal types so they can't drift
_LOAD_MODELS = get_args(LoadModel)
_PACING_KINDS = get_args(PacingKind)
_SLO_OPS = get_args(SloOp)
_WINDOW_KINDS = get_args(WindowKind)

_PROBES = {
    "client": ClientProbe,
    "top": KubectlTopProbe,
    "rss": PerWorkerRSSProbe,
    "restart": RestartProbe,
    "limits": ResourceLimitsProbe,
    "pods": PodCountProbe,
}

# probes that can observe a downstream Service (read its pods, no URL) —
# the subset allowed under `observe:`. client/metrics need the Service's handles.
_K8S_PROBES = {"top", "rss", "restart", "limits", "pods"}


def load_experiment(path: str, *, mock: bool = False) -> tuple[Experiment, str]:
    """Parse a run file → (Experiment, runs_dir). One config = one named experiment.

    ``mock=True`` swaps in ``MockWorkload`` instead of resolving the workload
    registry. Declared extension modules are still imported so custom probes and
    config validation use the same path as a real run.
    """
    config_path = Path(path).expanduser().resolve()
    raw = yaml.safe_load(config_path.read_text())
    _load_extensions(raw.get("extensions"))
    subj = raw["service"]
    subj_name = subj.get("name", "service")
    if "provisioner" in subj or "provisioner" in raw:
        raise ValueError(
            "`provisioner` was renamed `deployer`; configure it at the experiment root"
        )
    if "base_url" not in subj:
        raise ValueError("service needs a `base_url` (how to reach the service)")
    service = _parse_service(subj)
    deployer = _parse_deployer(raw.get("deployer"), service)

    wl_cfg = raw["workload"]
    workload = MockWorkload() if mock else build_workload(wl_cfg["name"], wl_cfg)

    if "constraints" in raw:
        raise ValueError(
            "`constraints:` was renamed `resources:` — the resource profiles "
            "(workers/cpu/memory/replicas) the grid sweeps"
        )
    resources = [_parse_resources(c) for c in raw.get("resources", [{}])]
    loads = _parse_loads(raw["load"])
    if "probes" in raw:
        raise ValueError(
            "`probes:` was removed — `client` is always recorded; put "
            "prometheus/top/rss/restart/limits/pods or registered custom probes under "
            "`observe:` (the Service is the entry that omits `k8s`)"
        )
    # client (load-gen's own inflight/sent) is harness-intrinsic → always on, not a
    # config knob. observe: is the one place for per-service resource observation.
    probes = [ClientProbe(), *_parse_observe(raw.get("observe"), service)]
    if "derived" in raw:
        raise ValueError(
            "`derived:` was removed — express server-side ratios and rates directly "
            "as PromQL queries on the `prometheus` probe"
        )
    caseset = _load_caseset_ref(raw.get("caseset"), config_path.parent)
    cases = _parse_cases(raw, caseset)
    mix = _parse_mix(raw, cases)
    slo = _parse_slo(raw.get("slo"))
    cooldown_s = float(raw.get("cooldown_s", 0.0))
    if cooldown_s < 0:
        raise ValueError("cooldown_s must be >= 0")
    _validate_producers(probes, workload)
    registry = _static_registry(probes, workload)
    facet_schema = caseset.facet_schema if caseset else FacetSchema.from_raw(raw.get("facets"))
    declared_facets = _declared_facet_pairs(facet_schema, cases, workload)
    # observed services close the `{service="…"}` label value space (the family-keyed
    # registry no longer carries a per-service entry to check against)
    declared_services = {p.labels["service"] for p in probes if "service" in p.labels}
    # (family, service) pairs whose series are per-pod ONLY: per_pod emits {pod,service}
    # series and no service-level aggregate, so a {service="…"} SLO on one would resolve
    # Missing → skipped on EVERY run (a gate that silently stops gating). Fail fast in
    # _validate_slo instead. A same-family non-per_pod probe for the service (programmatic
    # Experiments) restores the aggregate, hence the subtraction.
    summed = {
        (d.name, p.labels["service"])
        for p in probes
        if "service" in p.labels and not getattr(p, "_per_pod", False)
        for d in p.describe()
    }
    per_pod_only = {
        (d.name, p.labels["service"])
        for p in probes
        if "service" in p.labels and getattr(p, "_per_pod", False)
        for d in p.describe()
    } - summed
    _validate_slo(
        slo,
        registry,
        declared_facets,
        declared_services,
        loads,
        per_pod_only,
        cooldown_s=cooldown_s,
    )
    experiment = Experiment(
        service=service,
        deployer=deployer,
        workload=workload,
        resources=resources,
        loads=loads,
        probes=probes,
        cases=cases,
        mix=mix,
        facet_order=facet_schema.ordered_value_lists(),
        slo=slo,
        abort_on_fail=bool(raw.get("abort_on_fail", False)),
        strict_slo=bool(raw.get("strict_slo", False)),  # skip → run failure (default lenient)
        # experiment name = config `name` (the named experiment dir), default service slug
        name=str(raw.get("name") or _slug(subj_name)),
        observe_interval_s=float(raw.get("observe_interval_s", 5.0)),
        cooldown_s=cooldown_s,
        teardown=bool(raw.get("teardown", False)),
    )
    # `runs_dir` is the parent holding one dir per experiment (output_dir = legacy alias)
    return experiment, raw.get("runs_dir") or raw.get("output_dir") or "./runs"


def _slug(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in s).strip("-") or "perf"


def _load_extensions(value: object) -> None:
    """Import consumer modules whose import-time registrations extend the harness."""
    if value is None:
        return
    modules = [value] if isinstance(value, str) else value
    if not isinstance(modules, list) or not all(isinstance(m, str) and m for m in modules):
        raise ValueError("extensions must be a module name or a list of module names")
    for module in modules:
        try:
            importlib.import_module(module)
        except Exception as exc:
            raise ValueError(f"failed to import extension module {module!r}: {exc}") from exc


def _load_caseset_ref(value: object, config_dir: Path) -> CaseSet | None:
    """Load a canonical CaseSet referenced by the experiment config.

    Relative paths belong to the config file, not the caller's current working
    directory. The canonical loader and validator remain owned by spec-case.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError("caseset must be a non-empty path string")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = config_dir / path
    caseset = load_caseset(path)
    validate(caseset)
    return caseset


def _parse_cases(raw: dict, caseset: CaseSet | None = None) -> list[Case]:
    """Top-level ``cases:`` → the canonical Case pool. Backward-compat: no cases but a
    top-level ``payload``/``payload_file`` → one default Case; neither → empty (Engine
    uses one anonymous Case). An entry's ``weight`` is experiment usage, not part of
    the Case — ``_parse_mix`` lifts it into the Experiment's mix Overlay."""
    items = raw.get("cases")
    if caseset is not None:
        if "facets" in raw:
            raise ValueError(
                "top-level `facets:` cannot accompany `caseset:` — the canonical "
                "CaseSet owns its facet schema"
            )
        if "payload" in raw or "payload_file" in raw:
            raise ValueError(
                "top-level payload cannot accompany `caseset:` — select canonical "
                "cases under `cases:`"
            )
        if items is None:
            return list(caseset.cases)
        if not isinstance(items, list):
            raise ValueError("cases must be a list of canonical case selections")
        by_id = {case.id: case for case in caseset.cases}
        selected: list[Case] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("caseset selections must be mappings with an `id`")
            unknown = set(item) - {"id", "weight"}
            if unknown:
                raise ValueError(
                    f"caseset selection has unsupported fields {sorted(unknown)!r}; "
                    "only `id` and experiment-local `weight` are allowed"
                )
            case_id = item.get("id")
            if not isinstance(case_id, str) or not case_id:
                raise ValueError("caseset selection needs a non-empty string `id`")
            if case_id in seen:
                raise ValueError(f"duplicate caseset selection: {case_id}")
            if case_id not in by_id:
                raise ValueError(f"caseset selection {case_id!r} not found in {caseset.caseset!r}")
            selected.append(by_id[case_id])
            seen.add(case_id)
        if not selected:
            raise ValueError("caseset selection must not be empty")
        return selected
    if items:
        return [
            Case(
                id=str(cc.get("id", f"case{i}")),
                input=_load_input(cc.get("input_file"), cc.get("input")) or {},
                facets={k: str(v) for k, v in (cc.get("facets") or {}).items()},
            )
            for i, cc in enumerate(items)
        ]
    payload = _load_input(raw.get("payload_file"), raw.get("payload"))
    return [Case(id="default", input=payload)] if payload is not None else []


def _parse_mix(raw: dict, cases: list[Case]) -> Overlay:
    """Inline ``cases[].weight`` → an Overlay of load weight per case.id (unwritten → 1.0).

    The Case OBJECT still carries no weight (spec: weight is "how this experiment
    uses the case", not its identity) — but the config entry IS experiment usage,
    so the weight sits right on it instead of a separate top-level key to cross-
    reference. Fail fast: a mix that zeroes every case is an error (the Engine's
    ``random.choices`` needs a positive weight)."""
    if "mix" in raw:
        raise ValueError(
            "top-level `mix:` was removed — put `weight:` on each `cases:` entry "
            "(the entry is experiment usage; the Case itself still carries no weight)"
        )
    weights: dict[str, float] = {}
    for i, cc in enumerate(raw.get("cases") or []):
        if "weight" in cc:
            weights[str(cc.get("id", f"case{i}"))] = float(cc["weight"])
    mix = Overlay(weights)
    eff = mix.resolve([c.id for c in cases], default_of=lambda _: 1.0)
    if eff and not any(w > 0 for w in eff.values()):
        raise ValueError("case weights leave every case at 0 — at least one must be > 0")
    return mix


def _parse_facet_order(facets: dict | None) -> dict[str, list[str]]:
    """Top-level ``facets.<key>.{values, ordered}`` → value order for report sorting."""
    return FacetSchema.from_raw(facets).ordered_value_lists()


def _load_input(file: str | None, inline: dict | None) -> dict | None:
    if inline is not None:
        return inline
    if file:
        import json

        return json.loads(Path(file).expanduser().read_text())
    return None


def _parse_service(c: dict, fallback_environment: Environment | None = None) -> Service:
    name = str(c.get("name", ""))
    component = c.get("component") or {}
    repository = component.get("repository") or {}
    environment = c.get("environment") or {}
    return Service(
        name=name,
        component=Component(
            repository=Repository(
                forge=Forge(name=str(repository.get("forge", ""))),
                path=str(repository.get("path", "")),
            ),
            name=str(component.get("name") or name),
        ),
        environment=Environment(
            name=str(
                environment.get("name")
                or (fallback_environment.name if fallback_environment else "")
            ),
            kubeconfig=os.path.expanduser(
                str(
                    environment.get("kubeconfig")
                    or (fallback_environment.kubeconfig if fallback_environment else "")
                )
            ),
            context=(
                str(environment["context"])
                if environment.get("context")
                else (fallback_environment.context if fallback_environment else None)
            ),
        ),
        base_url=str(c.get("base_url", "")).rstrip("/"),
        headers={str(k): str(v) for k, v in (c.get("headers") or {}).items()},
        namespace=str(c.get("namespace", "")),
        k8s_selector=str(c.get("k8s_selector", "")),
        metrics_prefix=str(c.get("metrics_prefix", "")),
        metrics_port=int(c.get("metrics_port", 8000)),
        container=c.get("container"),
    )


def _parse_observe(items: list[dict] | None, root_service: Service) -> list[Probe]:
    """``observe:`` → the ONE place for per-service resource observation, self +
    downstream in one shape. Each item is a Service plus ``probes``; omitted
    Environment fields inherit from the experiment Service.
    ``prometheus`` embeds Prombed and requires declarative PromQL queries. Resource
    families remain service-labeled, so reports can compare the same signal across
    observed services."""
    out: list[Probe] = []
    for item in items or []:
        service_name = str(item["name"])
        service = (
            root_service
            if service_name == root_service.name
            else _parse_service(item, root_service.environment)
        )
        obsolete = {"derive", "scrape", "metrics_url", "k8s"} & item.keys()
        if obsolete:
            raise ValueError(
                f"observe[{service_name}]: removed keys {sorted(obsolete)!r}; configure "
                "PromQL under `probes: [{name: prometheus, queries: [...]}]`"
            )
        # per_pod: top/limits emit one {pod}-labeled series per pod instead of the
        # service-level sum (per-pod usage vs its OWN request/limit on one chart)
        per_pod = bool(item.get("per_pod", False))
        probes = item.get("probes") or ["top"]
        for probe_item in probes:
            if isinstance(probe_item, str):
                pname = probe_item
                options: dict[str, object] = {}
            elif isinstance(probe_item, dict):
                pname = probe_item.get("name")
                if not isinstance(pname, str) or not pname:
                    raise ValueError(
                        f"observe[{service_name}].probes entries need a string `name`: "
                        f"{probe_item!r}"
                    )
                options = {str(k): v for k, v in probe_item.items() if k != "name"}
            else:
                raise ValueError(
                    f"observe[{service_name}].probes entries must be names or mappings, "
                    f"got {probe_item!r}"
                )
            if pname in _K8S_PROBES:
                if options:
                    raise ValueError(
                        f"observe[{service_name}].probes[{pname}]: builtin probe has no options, "
                        f"got {sorted(options)}"
                    )
                out.append(_PROBES[pname](target_service=service, per_pod=per_pod))
            elif pname == "prometheus":
                queries = _parse_prometheus_queries(options.pop("queries", None), service_name)
                url = options.pop("url", None)
                if service != root_service and not url:
                    raise ValueError(
                        f"observe[{service_name}].probes[prometheus]: a downstream service "
                        "needs an explicit `url`"
                    )
                headers = options.pop("headers", None)
                if headers is not None and not isinstance(headers, dict):
                    raise ValueError(
                        f"observe[{service_name}].probes[prometheus].headers must be a mapping"
                    )
                allowed_limits = {
                    "timeout_ms",
                    "max_scrape_bytes",
                    "retention_ms",
                    "max_series",
                    "max_samples_per_series",
                }
                unknown = set(options) - allowed_limits
                if unknown:
                    raise ValueError(
                        f"observe[{service_name}].probes[prometheus]: unknown options "
                        f"{sorted(unknown)!r}"
                    )
                out.append(
                    PrometheusProbe(
                        service=service_name,
                        queries=queries,
                        url=str(url) if url else None,
                        headers={str(k): str(v) for k, v in (headers or {}).items()},
                        **{key: int(value) for key, value in options.items()},
                    )
                )
            elif pname == "client":
                raise ValueError(
                    f"observe[{service_name}].probes: 'client' is intrinsic and cannot "
                    "observe a service"
                )
            else:
                out.append(
                    build_probe(
                        pname,
                        ProbeConfig(
                            service=service,
                            per_pod=per_pod,
                            options=options,
                        ),
                    )
                )
    return out


def _parse_prometheus_queries(items: object, service: str) -> list[PrometheusQuery]:
    if not isinstance(items, list) or not items:
        raise ValueError(f"observe[{service}].probes[prometheus] needs a non-empty `queries` list")
    out: list[PrometheusQuery] = []
    seen = {"up"}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(
                f"observe[{service}].probes[prometheus].queries entries must be mappings"
            )
        name = item.get("name")
        promql = item.get("promql")
        if not isinstance(name, str) or not name:
            raise ValueError(f"Prometheus query needs a non-empty string `name`: {item!r}")
        if not isinstance(promql, str) or not promql:
            raise ValueError(f"Prometheus query {name!r} needs a non-empty string `promql`")
        if name in seen:
            raise ValueError(f"Prometheus query name {name!r} is duplicate or reserved")
        seen.add(name)
        kind = str(item.get("kind", "gauge"))
        if kind not in ("counter", "gauge"):
            raise ValueError(f"Prometheus query {name!r}: kind must be counter|gauge, got {kind!r}")
        labels = item.get("labels", [])
        if not (
            isinstance(labels, list)
            and all(isinstance(label, str) and label for label in labels)
            and len(set(labels)) == len(labels)
        ):
            raise ValueError(f"Prometheus query {name!r}: labels must be a list of unique names")
        unknown = set(item) - {"name", "promql", "kind", "unit", "description", "labels"}
        if unknown:
            raise ValueError(f"Prometheus query {name!r}: unknown keys {sorted(unknown)!r}")
        out.append(
            PrometheusQuery(
                name=name,
                promql=promql,
                value_kind=kind,  # type: ignore[arg-type]
                unit=str(item.get("unit", "")),
                description=str(item.get("description", "")),
                labels=tuple(labels),
            )
        )
    return out


def _parse_deployer(c: dict | None, service: Service) -> Deployer[Deployment] | None:
    """``deployer:`` → a Deployer, or None (the common case: the
    service is already deployed; ``resources:`` entries merely label the trials)."""
    if not c:
        return None
    kind = c.get("type", "helm")
    if kind == "helm":
        if not service.environment.kubeconfig or not service.namespace:
            raise ValueError(
                "a Helm deployer requires `service.environment.kubeconfig` and "
                "`service.namespace`"
            )
        return HelmDeployer(
            release=c["release"],
            chart_path=os.path.expanduser(c["chart_path"]),
            namespace=service.namespace,
            kubeconfig=service.environment.kubeconfig,
            base_values=_expand(c.get("base_values")),
            set_paths=c.get("set_paths"),
            rollout_timeout_s=int(c.get("rollout_timeout_s", 180)),
            extra_set=c.get("extra_set"),
        )
    raise ValueError(f"unknown deployer type: {kind!r} (only `helm`)")


def _parse_resources(c: dict) -> ResourceProfile:
    return ResourceProfile(
        cpu=c.get("cpu"),
        memory=c.get("memory"),
        workers=c.get("workers"),
        replicas=int(c.get("replicas", 1)),
        extra={k: str(v) for k, v in (c.get("extra") or {}).items()},
    )


def _parse_loads(c: dict) -> list[LoadProfile]:
    """``load:`` block → the Experiment's Load arms.

    Two ways to express the schedule, mutually exclusive:
      - ``stages:`` → one arm with an explicit shape (a step/spike curve).
      - ``levels:``/``level:`` (+ ``ramp_s``/``steady_s``) → one ramp→hold arm
        per level, the common capacity sweep. Here ``warmup_s`` defaults to the
        ramp window (the ramp is the warmup).
    Common across both: ``model`` (open/closed), ``pacing`` (closed), and
    ``max_inflight`` (open).
    """
    if "max_requests" in c:
        raise ValueError("load.max_requests is not supported by the Python implementation")

    model = c.get("model", "closed")
    if model not in _LOAD_MODELS:
        raise ValueError(f"load.model must be one of {_LOAD_MODELS}, got {model!r}")
    if c.get("stages") and ("levels" in c or "level" in c):
        raise ValueError("load: set either `stages` or `levels`/`level`, not both")
    pacing = _parse_pacing(c.get("pacing"))
    max_inflight = c.get("max_inflight")
    # mid-trial circuit breaker (open + closed): stop the arm early once the cumulative
    # error rate crosses this fraction — distinct from the between-trial abort_on_fail SLO
    aoer = c.get("abort_on_error_rate")
    abort_on_error_rate = float(aoer) if aoer is not None else None
    breaker_min_n = int(c.get("breaker_min_n", 20))
    graceful_stop_s = float(c.get("graceful_stop_s", 30.0))  # drain window before cancel
    # fail-fast on a nonsensical stop policy (a misconfigured breaker is worse than none:
    # 0 trips healthy traffic, >1 never trips, min_n<1 has no statistical floor)
    if abort_on_error_rate is not None and not 0 < abort_on_error_rate <= 1:
        raise ValueError(f"load.abort_on_error_rate must be in (0, 1]; got {abort_on_error_rate}")
    if breaker_min_n < 1:
        raise ValueError(f"load.breaker_min_n must be >= 1; got {breaker_min_n}")
    if graceful_stop_s < 0:
        raise ValueError(
            f"load.graceful_stop_s must be >= 0 (0 = hard stop); got {graceful_stop_s}"
        )

    if c.get("stages"):
        schedule = _parse_schedule(c["stages"], float(c.get("start_level", 0.0)))
        return [
            LoadProfile(
                model=model,
                schedule=schedule,
                pacing=pacing,
                warmup_s=float(c.get("warmup_s", 0.0)),
                max_inflight=max_inflight,
                abort_on_error_rate=abort_on_error_rate,
                breaker_min_n=breaker_min_n,
                graceful_stop_s=graceful_stop_s,
            )
        ]

    levels = c.get("levels") or [c.get("level", 10)]
    ramp_s = float(c.get("ramp_s", 20.0))
    steady_s = float(c.get("steady_s", 120.0))
    warmup_s = float(c.get("warmup_s", ramp_s))
    return [
        LoadProfile(
            model=model,
            schedule=Schedule.ramp_hold(float(lv), ramp_s, steady_s),
            pacing=pacing,
            warmup_s=warmup_s,
            max_inflight=max_inflight,
            abort_on_error_rate=abort_on_error_rate,
            breaker_min_n=breaker_min_n,
            graceful_stop_s=graceful_stop_s,
        )
        for lv in levels
    ]


def _parse_pacing(p: dict | None) -> Pacing:
    if not p:
        return Pacing()
    kind = p.get("kind", "none")
    if kind not in _PACING_KINDS:
        raise ValueError(f"load.pacing.kind must be one of {_PACING_KINDS}, got {kind!r}")
    return Pacing(
        kind=kind,
        secs=float(p.get("secs", 0.0)),
        max_secs=float(p.get("max_secs", 0.0)),
    )


def _parse_schedule(stages: list[dict], start_level: float) -> Schedule:
    """``stages:`` list → Schedule. Each item is ``{ramp_to, over_s}`` or
    ``{hold, for_s}``."""
    out: list[Stage] = []
    for s in stages:
        name = s.get("name")
        if "ramp_to" in s:
            out.append(
                Stage(
                    over_s=float(s["over_s"]), to_level=float(s["ramp_to"]), kind="ramp", name=name
                )
            )
        elif "hold" in s:
            out.append(
                Stage(over_s=float(s["for_s"]), to_level=float(s["hold"]), kind="hold", name=name)
            )
        else:
            raise ValueError(f"stage needs `ramp_to`+`over_s` or `hold`+`for_s`: {s!r}")
    return Schedule(stages=tuple(out), start_level=start_level)


def _parse_slo(items: list[dict] | None) -> list[SloAssertion]:
    """``slo:`` list → SloAssertions. Metric labels select entities; ``window``
    selects time. Each item carries exactly one comparison operator.
    Syntax only here; metric/label existence is checked in ``_validate_slo``."""
    out: list[SloAssertion] = []
    for s in items or []:
        metric = s.get("metric")
        if not isinstance(metric, str) or not metric:
            raise ValueError(f"slo entry needs a string `metric`: {s!r}")
        if "scope" in s:
            raise ValueError(
                "slo.scope was removed — put the slice in the metric as a label: "
                "facet:difficulty=simple → 'duration_ms{difficulty=\"simple\"}.p99'"
            )
        ops = [k for k in _SLO_OPS if k in s]
        if len(ops) != 1:
            raise ValueError(f"slo entry needs exactly one of {list(_SLO_OPS)}: {s!r}")
        op = ops[0]
        threshold = tuple(float(x) for x in s[op]) if op == "between" else float(s[op])
        window_raw = s.get("window") or {"kind": "measurement"}
        if not isinstance(window_raw, dict):
            raise ValueError("slo.window must be a mapping, e.g. {kind: hold}")
        kind = window_raw.get("kind", "measurement")
        if kind not in _WINDOW_KINDS:
            raise ValueError(f"slo.window.kind must be one of {list(_WINDOW_KINDS)}, got {kind!r}")
        level = window_raw.get("level")
        window = WindowSelector(
            kind=kind,
            name=window_raw.get("name"),
            level=float(level) if level is not None else None,
        )
        out.append(
            SloAssertion(
                metric=metric,
                op=op,
                threshold=threshold,
                window=window,
            )
        )
    return out


def _validate_producers(probes: list[Probe], workload: Workload) -> None:
    """Fail-fast (config time) on a producer that declares a metric family wrongly:
    a Workload may only declare ``request``-side distributions; a Probe only
    ``resource``-side families; no producer may shadow a builtin ``request.*`` family;
    and a family re-declared by two producers must agree on its metadata. Keeps the
    side → who-produces-it invariant honest (the report/SLO trust ``describe()``)."""
    builtin_request = {d.name for d in REQUEST_DESCRIPTORS}
    seen: dict[str, MetricFamily] = {}

    def _claim(fam: MetricFamily, who: str) -> None:
        if fam.name in builtin_request:
            raise ValueError(f"{who} may not declare the builtin request metric {fam.name!r}")
        prev = seen.get(fam.name)
        if prev is not None and prev != fam:
            raise ValueError(
                f"metric family {fam.name!r} declared with conflicting metadata: {prev} vs {fam}"
            )
        seen[fam.name] = fam

    for m in workload.describe():
        if m.side != "request" or m.value_kind != "distribution":
            raise ValueError(
                f"Workload.describe() may only declare request-side distributions; got "
                f"side={m.side!r} value_kind={m.value_kind!r} for {m.name!r}"
            )
        _claim(m, "Workload.describe()")
    for p in probes:
        for d in p.describe():
            if d.side != "resource":
                raise ValueError(
                    f"Probe {type(p).__name__}.describe() may only declare resource-side "
                    f"metrics; got side={d.side!r} for {d.name!r}"
                )
            _claim(d, f"Probe {type(p).__name__}.describe()")


def _static_registry(probes: list[Probe], workload: Workload) -> dict[str, MetricFamily]:
    """Statically-knowable metric FAMILIES for SLO validation: builtin ``request.*`` +
    each Probe's ``describe()`` + the Workload's declared per-request metrics. Keyed
    by family name so metadata is stored once rather than once per service."""
    reg: dict[str, MetricFamily] = {
        d.name: d for d in (*REQUEST_DESCRIPTORS, *PER_REQUEST_DESCRIPTORS)
    }
    for p in probes:
        reg.update({d.name: d for d in p.describe()})  # by family (dedup across services)
        # the Engine synthesizes `<family>.up` health series per probe — declare it
        # so observation availability can be an explicit SLO.
        reg[f"{p.family}.up"] = MetricFamily(
            name=f"{p.family}.up",
            unit="",
            side="resource",
            value_kind="gauge",
            source=p.source,
            description="probe health (1 ok / 0 failed) — mean 即观测可用率",
            labels=frozenset(p.labels),
        )
    reg.update({d.name: d for d in workload.describe()})
    return reg


def _declared_facet_pairs(
    facet_schema: FacetSchema, cases: list[Case], workload: Workload
) -> set[str]:
    """``k=v`` facet pairs an SLO scope may gate: the static Case mix + the
    Workload's declared runtime facets + any ``facets:`` block values."""
    pairs = {f"{k}={v}" for c in cases for k, v in c.facets.items()}
    for fd in workload.describe_facets():
        pairs.update(f"{fd.name}={v}" for v in fd.values)
    for key, spec in facet_schema.facets.items():
        pairs.update(f"{key}={v}" for v in spec.values or [])
    return pairs


def _validate_slo(
    slo: list[SloAssertion],
    registry: dict[str, MetricFamily],
    declared_facets: set[str],
    declared_services: set[str],
    loads: list[LoadProfile],
    per_pod_only: set[tuple[str, str]] = frozenset(),  # (family, service) with no aggregate
    *,
    cooldown_s: float = 0.0,
) -> None:
    """Fail fast on a misconfigured gate (a perf gate must not silently skip).

    One rule per ``side`` (the family declares it; no name-pattern special cases):
      - resource side — selected by a ``{service=…}`` label: explicit legal stat,
        observed service, and the service must have an
        aggregate series (a ``per_pod``-only family would skip every run).
      - request side — facet labels select a slice: at most ONE (the report is
        a marginal pivot, not a cube) and its value must be one a run produces.
    ``value_kind`` gates the stat either way (``validate_ref``)."""
    planned = [stage for load in loads for stage in load.schedule.stages]
    for a in slo:
        name, labels, stat = parse_ref(a.metric)
        fam = registry.get(name)
        non_service_labels = {k: v for k, v in labels.items() if k != "service"}

        if a.window.kind in ("ramp", "hold"):
            candidates = [stage for stage in planned if stage.kind == a.window.kind]
            if a.window.name is not None:
                candidates = [stage for stage in candidates if stage.label == a.window.name]
            if a.window.level is not None:
                candidates = [stage for stage in candidates if stage.to_level == a.window.level]
            if not candidates:
                raise ValueError(f"slo.window {a.window!r} matches no configured stage")
        elif a.window.name is not None or a.window.level is not None:
            raise ValueError("slo.window name/level only apply to ramp or hold windows")

        if a.window.kind == "cooldown":
            if cooldown_s <= 0:
                raise ValueError(
                    f"slo.metric {a.metric!r}: cooldown window requires cooldown_s > 0"
                )
            if fam is None or fam.side != "resource":
                raise ValueError(
                    f"slo.metric {a.metric!r}: cooldown window only supports "
                    "resource-side time-sampled metrics"
                )
            if fam.value_kind not in ("gauge", "counter"):
                raise ValueError(
                    f"slo.metric {a.metric!r}: cooldown window needs a raw "
                    f"gauge/counter series, got {fam.value_kind}"
                )

        if fam is not None and fam.side == "resource":
            if stat is None:
                raise ValueError(
                    f"slo.metric {a.metric!r}: a resource metric needs an explicit "
                    "stat ('<name>{labels}.<stat>')"
                )
            unknown_labels = set(labels) - fam.labels
            if unknown_labels:
                raise ValueError(
                    f"slo.metric {a.metric!r}: unknown labels {sorted(unknown_labels)} "
                    f"for {name!r}; declared labels: {sorted(fam.labels) or 'none'}"
                )
            validate_ref(a.metric, registry)  # stat legal for the family's value_kind
            service = labels.get("service")
            if service is not None and service not in declared_services:
                raise ValueError(
                    f"slo.metric {a.metric!r}: service {service!r} not observed "
                    f"(observe: {sorted(declared_services) or 'none'})"
                )
            if service is not None and (name, service) in per_pod_only:
                raise ValueError(
                    f"slo.metric {a.metric!r}: service {service!r} is observed "
                    f"per_pod — only {{pod,service}} series exist (no service-level "
                    f"aggregate), so this gate would skip every run; drop per_pod for "
                    f"that service or remove this SLO"
                )
            continue

        # request side (no service label): a builtin alias, or a declared family + legal stat
        if "service" in labels:
            raise ValueError(
                f"slo.metric {a.metric!r}: a `service` label is only valid on a "
                f"resource-side metric; {name!r} is request-side"
            )
        slice_labels = non_service_labels
        if len(slice_labels) > 1:
            raise ValueError(
                f"slo.metric {a.metric!r}: at most one facet slice label "
                f"(report is a marginal pivot, not a cube); got {sorted(slice_labels)}"
            )
        if stat is None:
            if name not in SLO_METRICS:
                raise ValueError(
                    f"slo.metric {a.metric!r}: {name!r} is not a builtin field "
                    f"{sorted(SLO_METRICS)} (for a probe/per-request metric use '<name>.<stat>')"
                )
        elif fam is not None:
            validate_ref(a.metric, registry)  # stat legal for the family's value_kind
            if fam.side == "resource" and slice_labels:
                raise ValueError(
                    f"slo.metric {name!r} is a resource-side metric — trial-global, "
                    f"can't be sliced by {sorted(slice_labels)}"
                )
        else:
            # an SLO gate must fail-fast, not silently skip a typo → the metric must be
            # DECLARED (builtin / probe / framework ttft_ms / Workload.describe()).
            # A dynamic per-request metric still reaches the report/CSV — just can't gate.
            raise ValueError(
                f"slo.metric {a.metric!r}: {name!r} is not a declared metric — only declared "
                "metrics can gate (declare a per-request metric via Workload.describe(); a "
                "dynamic first_<event>_ms reaches the report but can't gate)"
            )

        # the facet label value must be one a run actually produces
        for k, v in slice_labels.items():
            if f"{k}={v}" not in declared_facets:
                raise ValueError(
                    f"slo.metric {a.metric!r}: facet {k}={v} unknown "
                    f"(declared: {sorted(declared_facets) or 'none'})"
                )


def _expand(p: str | None) -> str | None:
    return os.path.expanduser(p) if p else None
