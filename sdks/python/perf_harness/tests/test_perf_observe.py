"""observe: — the one key for per-service resource observation (Service + downstream).

client (load-gen inflight/sent) is always recorded; there is no top-level probes:.
"""

import json
from types import SimpleNamespace

import pytest
from harness_toolbox.environment import KubernetesEnvironment

from perf_harness.config import load_experiment
from perf_harness.drive.load import LoadPlan, Warmup
from perf_harness.drive.runner import Runner
from perf_harness.engine import Engine, Experiment
from perf_harness.metric import (
    GaugeSummary,
    series_id,
    split_ref,
    split_series,
)
from perf_harness.model import Outcome, ResourceProfile, Service
from perf_harness.observe import (
    FamilySpec,
    KubectlTopProbe,
    PodCountProbe,
    Probe,
    PrometheusQueryProbe,
    ResourceLimitsProbe,
)

_SERVICE = (
    "name: x\nprometheus: {url: http://prom}\n"
    "service: { name: chat, base_url: 'http://x:8001',\n"
    "  environment: { name: dev, kubeconfig: ~/.kube/d },\n"
    "  namespace: ns, k8s_selector: app=chat }\n"
    "resources: [ {} ]\n"
    "runner: { name: mock }\n"
    "load: { request_rate: inf, max_inflight: 1, hold_s: 0.2 }\n"
)


def _k8s_service(name: str, selector: str) -> Service:
    return Service(
        name=name,
        environment=KubernetesEnvironment(name="test", kubeconfig="/kc"),
        namespace="ns",
        k8s_selector=selector,
    )


def test_k8s_probe_binds_to_service_as_a_label():
    service = _k8s_service("planit", "app=planit")
    p = KubectlTopProbe(target_service=service)
    assert p.name == "top.planit"  # unique store id (one per observed target)
    assert p.family == "top" and p.labels == {"service": "planit"}  # service is a LABEL
    assert p._target_service is service
    descs = p.describe()
    # describe() returns the FAMILY (label-free); the concrete series id is built from
    # family + the probe's labels (service lives on the series, not the family)
    assert {d.name for d in descs} == {"top.cpu_m", "top.mem_mi"}
    assert {series_id(d.name, p.labels) for d in descs} == {
        'top.cpu_m{service="planit"}',
        'top.mem_mi{service="planit"}',
    }
    assert all(d.side == "resource" and d.source == "k8s" for d in descs)
    # unbound probe keeps the bare name and falls back to the Service's k8s
    bare = KubectlTopProbe()
    assert bare.name == "top" and bare._target_service is None


def test_observe_is_the_one_shape_with_client_always_on(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        _SERVICE + "observe:\n"
        "  - name: chat\n"
        "    probes:\n"
        "      - { name: prometheus_query, queries: [ { name: requests, promql: 'sum(requests_total)', kind: counter } ] }\n"
        "      - top\n"
        "      - rss\n"
        "  - { name: planit, namespace: ns, k8s_selector: app=planit, probes: [top, rss] }\n"
        "  - { name: executor, namespace: ns, k8s_selector: app=exec, probes: [top] }\n"
    )
    exp, _ = load_experiment(str(cfg))
    by_name = {p.name: p for p in exp.probes}
    # client is auto-prepended (always on); every observed probe is service-prefixed
    assert [p.name for p in exp.probes] == [
        "client",
        "prometheus_query.chat",
        "top.chat",
        "rss.chat",
        "top.planit",
        "rss.planit",
        "top.executor",
    ]
    assert by_name["top.chat"]._target_service is exp.service
    assert by_name["top.planit"]._target_service.k8s_selector == "app=planit"


def test_probes_key_is_removed(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(_SERVICE + "probes: [client, top]\n")
    try:
        load_experiment(str(cfg))
    except ValueError as e:
        assert "probes" in str(e) and "observe" in str(e)
    else:
        raise AssertionError("expected ValueError: probes: was removed")


def test_observe_downstream_metric_uses_service_workload(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        _SERVICE + "observe:\n"
        "  - name: planit\n"
        "    namespace: ns\n"
        "    workloads:\n"
        "      - {name: planit, namespace: ns, location: {kind: labels, labels: {app: planit}}}\n"
        "    probes:\n"
        "      - {name: prometheus_query, queries: [{name: requests, promql: 'sum(requests_total)'}]}\n"
    )
    exp, _ = load_experiment(str(cfg))
    probe = next(p for p in exp.probes if isinstance(p, PrometheusQueryProbe))
    assert probe.labels == {"service": "planit"}
    assert probe.data_source.url == "http://prom"


def test_observe_rejects_unknown_probe(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        _SERVICE + "observe:\n"
        "  - { name: planit, namespace: ns, k8s_selector: app=planit, probes: [client] }\n"
    )
    try:
        load_experiment(str(cfg))
    except ValueError as e:
        assert "client" in str(e)
    else:
        raise AssertionError("expected ValueError for a non-service probe in observe")


def test_resource_limits_probe_describes_request_and_limit_gauges():
    # request/limit are just metrics whose series never varies — gauges, k8s-sourced,
    # and (crucially) the SAME units as `top`, so the report overlays them as flat
    # reference lines on the cpu/mem usage charts with no special rendering.
    service = _k8s_service("chat", "app=chat")
    p = ResourceLimitsProbe(target_service=service)
    assert p.family == "limits"
    descs = {d.name: d for d in p.describe()}
    assert set(descs) == {
        "limits.cpu_request",
        "limits.cpu_limit",
        "limits.mem_request",
        "limits.mem_limit",
    }
    assert all(d.value_kind == "gauge" and d.source == "k8s" for d in descs.values())
    top = KubectlTopProbe(target_service=service).families
    assert descs["limits.cpu_limit"].unit == top["cpu_m"].unit  # millicores → same cpu chart
    assert descs["limits.mem_limit"].unit == top["mem_mi"].unit  # MiB → same mem chart


def test_observe_wires_limits_probe(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(_SERVICE + "observe:\n  - { name: chat, probes: [top, limits] }\n")
    exp, _ = load_experiment(str(cfg))
    assert "limits.chat" in [p.name for p in exp.probes]  # service-bound limits probe wired


def test_observe_wires_pod_count_probe(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(_SERVICE + "observe:\n  - { name: chat, probes: [pods] }\n")
    exp, _ = load_experiment(str(cfg))
    probe = next(p for p in exp.probes if isinstance(p, PodCountProbe))
    assert probe.name == "pods.chat"
    assert {d.name for d in probe.describe()} == {"pods.count"}


def test_series_id_is_prometheus_form():
    assert series_id("top.cpu_m", {}) == "top.cpu_m"
    assert series_id("top.cpu_m", {"service": "chat"}) == 'top.cpu_m{service="chat"}'


def test_resolver_splits_labeled_series_id():
    # the stat is still the last dotted segment, so the resolver works on labeled ids
    name, stat = split_ref('top.cpu_m{service="planit"}.peak')
    assert name == 'top.cpu_m{service="planit"}' and stat == "peak"


def test_store_pivot_groups_by_service_label():
    # the group-by-label read every consumer uses (analysis peaks, §3 curves):
    # one summary per service for a family
    from perf_harness.metric.store import MetricStore

    pm = {
        'top.cpu_m{service="chat"}': GaugeSummary(last=300, mean=250, peak=305),
        'top.cpu_m{service="planit"}': GaugeSummary(last=460, mean=420, peak=467),
    }
    r = SimpleNamespace(metrics={})
    out = MetricStore([]).pivot(r, "top.cpu_m", "service", SimpleNamespace(probe_metrics=pm))
    assert {svc: s.peak for svc, s in out.items()} == {"chat": 305, "planit": 467}


def test_store_pivot_keeps_per_pod_entities_distinct():
    # per_pod adds a {pod} label; the pivot must not drop or merge those series —
    # each pod is its own entity, keyed "service/pod"
    from perf_harness.metric.store import MetricStore

    pm = {
        'top.cpu_m{pod="chat-a",service="chat"}': GaugeSummary(last=1, peak=305),
        'top.cpu_m{pod="chat-b",service="chat"}': GaugeSummary(last=1, peak=467),
    }
    r = SimpleNamespace(metrics={})
    out = MetricStore([]).pivot(r, "top.cpu_m", "service", SimpleNamespace(probe_metrics=pm))
    assert {svc: s.peak for svc, s in out.items()} == {
        "chat/chat-a": 305,
        "chat/chat-b": 467,
    }


def test_split_series_is_series_id_inverse():
    assert split_series('cpu_m{pod="a",service="chat"}') == (
        "cpu_m",
        {"pod": "a", "service": "chat"},
    )
    # a dotted bare name has no stat to mis-split (unlike parse_ref on a ref)
    assert split_series("client.inflight") == ("client.inflight", {})


async def test_top_probe_per_pod_emits_labeled_keys(monkeypatch):
    async def fake_run(cmd):  # two replicas in the `kubectl top pod -l …` output
        return "chat-abc 850m 1234Mi\nchat-def 150m 766Mi\n"

    monkeypatch.setattr("perf_harness.observe.k8s.run_capture", fake_run)
    service = _k8s_service("chat", "app=chat")
    p = KubectlTopProbe(target_service=service, per_pod=True)
    out = await p.sample(SimpleNamespace(service=Service()))
    # one {pod}-labeled key per replica — NOT the summed cpu_m/mem_mi
    assert out == {
        'cpu_m{pod="chat-abc"}': 850.0,
        'mem_mi{pod="chat-abc"}': 1234.0,
        'cpu_m{pod="chat-def"}': 150.0,
        'mem_mi{pod="chat-def"}': 766.0,
    }


async def test_limits_probe_per_pod_and_summed_share_one_source(monkeypatch):
    j = json.dumps(
        {
            "items": [
                {
                    "metadata": {"name": "chat-abc"},
                    "spec": {"containers": [{"resources": {"limits": {"cpu": "1"}}}]},
                },
                {
                    "metadata": {"name": "chat-def"},
                    "spec": {"containers": [{"resources": {"limits": {"cpu": "2"}}}]},
                },
            ]
        }
    )

    async def fake_run(service, ctx):
        return json.loads(j)

    monkeypatch.setattr("perf_harness.observe.k8s._pod_snapshot", fake_run)
    service = _k8s_service("chat", "app=chat")
    ctx = SimpleNamespace(service=Service())
    per_pod = await ResourceLimitsProbe(target_service=service, per_pod=True).sample(ctx)
    assert per_pod == {
        'cpu_limit{pod="chat-abc"}': 1000.0,
        'cpu_limit{pod="chat-def"}': 2000.0,
    }
    # summed = sum of the same per-pod parse (the service-level total)
    summed = await ResourceLimitsProbe(target_service=service).sample(ctx)
    assert summed == {"cpu_limit": 3000.0}


async def test_limits_probe_refreshes_dynamic_pod_set(monkeypatch):
    responses = iter(
        [
            json.dumps(
                {
                    "items": [
                        {
                            "metadata": {"name": "chat-a"},
                            "spec": {"containers": [{"resources": {"limits": {"cpu": "1"}}}]},
                        }
                    ]
                }
            ),
            json.dumps(
                {
                    "items": [
                        {
                            "metadata": {"name": "chat-a"},
                            "spec": {"containers": [{"resources": {"limits": {"cpu": "1"}}}]},
                        },
                        {
                            "metadata": {"name": "chat-b"},
                            "spec": {"containers": [{"resources": {"limits": {"cpu": "1"}}}]},
                        },
                    ]
                }
            ),
        ]
    )

    async def fake_run(service, ctx):
        return json.loads(next(responses))

    monkeypatch.setattr("perf_harness.observe.k8s._pod_snapshot", fake_run)
    service = _k8s_service("chat", "app=chat")
    probe = ResourceLimitsProbe(target_service=service)
    ctx = SimpleNamespace(service=Service())
    assert await probe.sample(ctx) == {"cpu_limit": 1000.0}
    assert await probe.sample(ctx) == {"cpu_limit": 2000.0}


class _FanProbe(Probe):
    """A probe whose sample keys carry a {pod} label — the fan-out contract."""

    name = "fan"
    source = "k8s"
    _service = "chat"
    families = {"cpu_m": FamilySpec("millicores")}

    async def sample(self, ctx):
        return {
            series_id("cpu_m", {"pod": "p0"}): 100.0,
            series_id("cpu_m", {"pod": "p1"}): 200.0,
        }


class _NoopWL(Runner):
    name = "noop"

    async def fire(self, ctx):
        return Outcome(status=200, duration_ms=1.0)


async def test_engine_fans_pod_labeled_keys_into_per_pod_series():
    exp = Experiment(
        service=Service("m", base_url="http://127.0.0.1:0"),
        runner=_NoopWL(),
        resources=[ResourceProfile()],
        loads=[
            LoadPlan(
                warmup=Warmup(step_s=0),
                request_rate=float("inf"),
                max_inflight=2,
                hold_s=(0.0 + 0.2),
            )
        ],
        probes=[_FanProbe()],
    )
    r = (await Engine(exp).run()).arm_runs[0]
    # each pod becomes its own series, base {service} merged with the key's {pod}
    # (plus the synthesized health series at the probe's base label-set)
    sids = {sid for sid in r.series if sid.startswith("fan.")}
    assert sids == {
        'fan.cpu_m{pod="p0",service="chat"}',
        'fan.cpu_m{pod="p1",service="chat"}',
        'fan.up{service="chat"}',
    }
    # …and its own summary (constant gauges → peak == the per-pod constant)
    assert r.measurement.probe_metrics['fan.cpu_m{pod="p0",service="chat"}'].peak == 100.0
    assert r.measurement.probe_metrics['fan.cpu_m{pod="p1",service="chat"}'].peak == 200.0
    # the registry stays family-keyed — pod is a label, not a new family
    assert r.metrics["fan.cpu_m"].side == "resource"


def test_observe_per_pod_wires_the_flag(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        _SERVICE + "observe:\n  - { name: chat, probes: [top, limits], per_pod: true }\n"
    )
    exp, _ = load_experiment(str(cfg))
    by_name = {p.name: p for p in exp.probes}
    assert by_name["top.chat"]._per_pod and by_name["limits.chat"]._per_pod


def test_observe_prometheus_wires_queries_and_report_labels(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        _SERVICE
        + 'observe:\n- name: chat\n  probes:\n  - name: prometheus_query\n    queries:\n    - name: requests_by_path\n      promql: sum by (path) (requests_total)\n      kind: counter\n      unit: count\n      labels:\n      - path\n  - top\nreport:\n  columns:\n  - title: metric0\n    metric: prometheus_query.requests_by_path{service="chat",path="/v1"}.rate\n'
    )
    exp, _ = load_experiment(str(cfg))
    probe = next(p for p in exp.probes if p.name == "prometheus_query.chat")
    assert probe.queries[0].promql == "sum by (path) (requests_total)"
    assert probe.queries[0].labels == ("path",)
    assert (
        exp.report_columns[0].metric
        == 'prometheus_query.requests_by_path{service="chat",path="/v1"}.rate'
    )


def test_observe_rejects_removed_scrape_surface(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        _SERVICE + "observe:\n"
        "  - name: chat\n"
        "    probes: [top]\n"
        "    scrape: [ { from: x_total } ]\n"
    )
    with pytest.raises(ValueError, match="removed keys.*scrape"):
        load_experiment(str(cfg))


class _FlakyProbe(Probe):
    """Fails every tick — the observation-failure census must surface it."""

    name = "flaky.chat"
    source = "http"
    _service = "chat"

    families = {"x": FamilySpec("count")}

    @property
    def family(self) -> str:
        return "flaky"

    async def sample(self, ctx):
        raise RuntimeError("metrics endpoint down")


async def test_probe_errors_flow_to_arm_run_and_store():
    from perf_harness.metric import Missing
    from perf_harness.metric.store import MetricStore

    exp = Experiment(
        service=Service("m", base_url="http://127.0.0.1:0"),
        runner=_NoopWL(),
        resources=[ResourceProfile()],
        loads=[
            LoadPlan(
                warmup=Warmup(step_s=0),
                request_rate=float("inf"),
                max_inflight=1,
                hold_s=(0.0 + 0.2),
            )
        ],
        probes=[_FlakyProbe()],
        observe_interval_s=0.05,
    )
    r = (await Engine(exp).run()).arm_runs[0]
    pe = r.probe_errors["flaky.chat"]
    assert pe.failures == pe.ticks >= 1 and "down" in pe.last
    # an absent read on an errored probe is Missing(probe_error) — NOT "no slice"
    read = MetricStore([r]).query(r, 'flaky.x{service="chat"}.peak')
    assert isinstance(read, Missing) and read.reason == "probe_error"


def test_prometheus_query_replaces_derived_metrics(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        _SERVICE
        + 'observe:\n- name: chat\n  probes:\n  - name: prometheus_query\n    queries:\n    - name: x_mean_s\n      promql: sum(rate(x_seconds_sum[1m])) / sum(rate(x_seconds_count[1m]))\n      unit: s\nreport:\n  columns:\n  - title: metric0\n    metric: prometheus_query.x_mean_s{service="chat"}.mean\n'
    )
    exp, _ = load_experiment(str(cfg))
    probe = next(p for p in exp.probes if p.name == "prometheus_query.chat")
    assert probe.data_source.options.timeout_ms == 5000
    assert probe.queries[0].promql.startswith("sum(rate(")
    assert exp.report_columns[0].metric == 'prometheus_query.x_mean_s{service="chat"}.mean'


def test_top_level_derived_is_removed(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(_SERVICE + "derived: [ { as: bad, ratio: [nope, also_nope] } ]\n")
    with pytest.raises(ValueError, match="removed.*PromQL"):
        load_experiment(str(cfg))


async def test_up_series_synthesized_per_probe():
    # the Prometheus `up` analogue: health is a SERIES (when did it break), not just
    # the arm_run census. Healthy probe → all 1s; flaky probe → 0s and mean < 1.
    exp = Experiment(
        service=Service("m", base_url="http://127.0.0.1:0"),
        runner=_NoopWL(),
        resources=[ResourceProfile()],
        loads=[
            LoadPlan(
                warmup=Warmup(step_s=0),
                request_rate=float("inf"),
                max_inflight=1,
                hold_s=(0.0 + 0.2),
            )
        ],
        probes=[_FanProbe(), _FlakyProbe()],
        observe_interval_s=0.05,
    )
    r = (await Engine(exp).run()).arm_runs[0]
    healthy = r.measurement.probe_metrics[series_id("fan.up", {"service": "chat"})]
    assert healthy.mean == 1.0 and healthy.peak == 1.0
    broken = r.measurement.probe_metrics[series_id("flaky.up", {"service": "chat"})]
    assert broken.mean == 0.0  # every tick failed
    assert all(s.value == 0.0 for s in r.series[series_id("flaky.up", {"service": "chat"})].samples)
    assert r.metrics["flaky.up"].value_kind == "gauge"  # registered family


def test_prometheus_query_may_not_shadow_up(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        _SERVICE + "observe:\n"
        "  - name: chat\n"
        "    probes:\n"
        "      - name: prometheus_query\n"
        "        queries: [ { name: up, promql: 'sum(x_total)' } ]\n"
    )
    with pytest.raises(ValueError, match="up"):
        load_experiment(str(cfg))


def test_up_is_report_addressable(tmp_path):
    # observation availability can be selected explicitly in reports
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        _SERVICE
        + 'observe:\n- name: chat\n  probes:\n  - top\nreport:\n  columns:\n  - title: metric0\n    metric: top.up{service="chat"}.mean\n'
    )
    exp, _ = load_experiment(str(cfg))
    assert exp.report_columns[0].metric == 'top.up{service="chat"}.mean'


def test_limits_colors_are_report_palette():
    # chart colors are report-side display policy, never model metadata: the limits
    # reference lines pin limit=red / request=orange; everything else rotates
    from perf_harness.report import family_color

    assert family_color("limits.cpu_limit") == "#d62728"
    assert family_color("limits.mem_limit") == "#d62728"
    assert family_color("limits.cpu_request") == "#ff7f0e"
    assert family_color("top.cpu_m") == ""
