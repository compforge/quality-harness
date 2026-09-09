"""observe: — the one key for per-service resource observation (Service + downstream).

client (load-gen inflight/sent) is always recorded; there is no top-level probes:.
"""

import json
from types import SimpleNamespace

import pytest

from perf_harness.config import load_experiment
from perf_harness.drive.load import LoadProfile, Schedule
from perf_harness.drive.workload import Workload
from perf_harness.engine import Engine, Experiment
from perf_harness.metric import (
    GaugeSummary,
    series_id,
    split_ref,
    split_series,
)
from perf_harness.model import Environment, Outcome, ResourceProfile, Service
from perf_harness.observe import (
    FamilySpec,
    KubectlTopProbe,
    PodCountProbe,
    Probe,
    PrometheusProbe,
    PrometheusQuery,
    ResourceLimitsProbe,
)

_SERVICE = (
    "name: x\n"
    "service: { name: chat, base_url: 'http://x:8001',\n"
    "  environment: { name: dev, kubeconfig: ~/.kube/d },\n"
    "  namespace: ns, k8s_selector: app=chat }\n"
    "resources: [ {} ]\n"
    "workload: { name: mock }\n"
    "load: { model: closed, levels: [1], ramp_s: 0, steady_s: 0.2 }\n"
)


def _k8s_service(name: str, selector: str) -> Service:
    return Service(
        name=name,
        environment=Environment(name="test", kubeconfig="/kc"),
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
        "      - { name: prometheus, queries: [ { name: requests, promql: 'sum(requests_total)', kind: counter } ] }\n"
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
        "prometheus.chat",
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


def test_observe_downstream_prometheus_requires_url(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        _SERVICE + "observe:\n"
        "  - name: planit\n"
        "    namespace: ns\n"
        "    k8s_selector: app=planit\n"
        "    probes:\n"
        "      - { name: prometheus, queries: [ { name: requests, promql: 'sum(requests_total)' } ] }\n"
    )
    try:
        load_experiment(str(cfg))
    except ValueError as e:
        assert "prometheus" in str(e) and "url" in str(e)
    else:
        raise AssertionError("expected ValueError: downstream prometheus needs URL")


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


def test_slo_service_label_only_on_time_sampled(tmp_path):
    """A `service` label belongs to a resource (time_sampled) series only — never a
    request slice. The family-keyed registry no longer fail-fasts these for free, so
    _validate_slo pins the rule down explicitly (else they'd silently go missing or
    crash the resolver)."""
    base = _SERVICE + "observe:\n  - { name: chat, probes: [top] }\n"

    def cfg(metric: str) -> str:
        c = tmp_path / "c.yaml"
        # single-quote the metric: it carries `{...}` and `"` that YAML flow syntax
        # would otherwise choke on
        c.write_text(base + f"slo: [ {{ metric: '{metric}', lt: 100 }} ]\n")
        return str(c)

    # valid: a resource metric with a service label + an explicit stat
    exp, _ = load_experiment(cfg('top.cpu_m{service="chat"}.peak'))
    assert exp.slo[0].metric == 'top.cpu_m{service="chat"}.peak'

    # a request (derived) metric can NOT carry a service label
    with pytest.raises(ValueError, match="service"):
        load_experiment(cfg('request.duration_ms{service="chat"}.p99'))

    # a builtin alias + service (no stat) is rejected too (would crash the resolver)
    with pytest.raises(ValueError, match="service"):
        load_experiment(cfg('p99_ms{service="chat"}'))

    # an unobserved service value still fails
    with pytest.raises(ValueError, match="not observed"):
        load_experiment(cfg('top.cpu_m{service="planit"}.peak'))


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

    async def fake_run(cmd):
        return j

    monkeypatch.setattr("perf_harness.observe.k8s.run_capture", fake_run)
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

    async def fake_run(cmd):
        return next(responses)

    monkeypatch.setattr("perf_harness.observe.k8s.run_capture", fake_run)
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


class _NoopWL(Workload):
    name = "noop"

    async def fire(self, ctx):
        return Outcome(status=200, duration_ms=1.0)


async def test_engine_fans_pod_labeled_keys_into_per_pod_series():
    exp = Experiment(
        service=Service("m", base_url="http://127.0.0.1:0"),
        workload=_NoopWL(),
        resources=[ResourceProfile()],
        loads=[LoadProfile(model="closed", schedule=Schedule.ramp_hold(2, 0.0, 0.2))],
        probes=[_FanProbe()],
    )
    r = (await Engine(exp).run()).trials[0]
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


def test_slo_service_gate_fails_fast_under_per_pod(tmp_path):
    # per_pod emits only {pod,service} series — a {service="…"} gate would resolve
    # Missing → skipped on EVERY run (and strict_slo defaults false, so an existing CI
    # gate would silently stop gating). That must be a config-time error, not a skip.
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        _SERVICE + "observe:\n  - { name: chat, probes: [top], per_pod: true }\n"
        "slo: [ { metric: 'top.cpu_m{service=\"chat\"}.peak', lt: 100 } ]\n"
    )
    with pytest.raises(ValueError, match="per_pod"):
        load_experiment(str(cfg))


def test_observe_per_pod_wires_the_flag(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        _SERVICE + "observe:\n  - { name: chat, probes: [top, limits], per_pod: true }\n"
    )
    exp, _ = load_experiment(str(cfg))
    by_name = {p.name: p for p in exp.probes}
    assert by_name["top.chat"]._per_pod and by_name["limits.chat"]._per_pod


async def test_prometheus_probe_evaluates_promql_queries():
    import httpx

    text = (
        'gen_ai_server_request_duration_seconds_count{gen_ai_operation_name="chat",error_type=""} 90\n'
        'gen_ai_server_request_duration_seconds_count{gen_ai_operation_name="chat",error_type="X"} 10\n'
    )

    def handler(_request):
        return httpx.Response(200, content=text.encode())

    p = PrometheusProbe(
        service="chat",
        queries=[
            PrometheusQuery(
                name="sse_streams",
                promql="sum(gen_ai_server_request_duration_seconds_count)",
                value_kind="counter",
                unit="count",
            ),
            PrometheusQuery(
                name="sse_errors",
                promql=(
                    'sum(gen_ai_server_request_duration_seconds_count{error_type!="",'
                    'error_type!="client_disconnect"})'
                ),
                value_kind="counter",
                unit="count",
            ),
        ],
    )
    assert {d.name for d in p.describe()} == {
        "prometheus.sse_streams",
        "prometheus.sse_errors",
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ctx = SimpleNamespace(service=Service(base_url="http://x"), probe_client=client, t0=1.0)
        out = await p.sample(ctx)
    assert out == {"sse_streams": 100.0, "sse_errors": 10.0}


async def test_prometheus_probe_emits_declared_vector_labels():
    import httpx

    text = (
        'control_requests_total{path="/v1/bots",method="POST",status_code="200"} 30\n'
        'control_requests_total{path="/v1/bots",method="GET",status_code="200"} 12\n'
        'control_requests_total{path="/v1/configs",method="POST",status_code="200"} 7\n'
    )

    def handler(_request):
        return httpx.Response(200, content=text.encode())

    p = PrometheusProbe(
        service="control",
        queries=[
            PrometheusQuery(
                name="ctl_requests",
                promql="sum by (path) (control_requests_total)",
                value_kind="counter",
                labels=("path",),
            ),
        ],
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ctx = SimpleNamespace(service=Service(base_url="http://x"), probe_client=client, t0=1.0)
        out = await p.sample(ctx)
    assert out == {
        'ctl_requests{path="/v1/bots"}': 42.0,
        'ctl_requests{path="/v1/configs"}': 7.0,
    }


async def test_downstream_prometheus_does_not_receive_subject_credentials():
    import httpx

    seen_headers = None

    def handler(request):
        nonlocal seen_headers
        seen_headers = request.headers
        return httpx.Response(200, content=b"requests_total 1\n")

    probe = PrometheusProbe(
        service="worker",
        url="http://worker/metrics",
        headers={"X-Metrics-Token": "metrics-secret"},
        queries=[PrometheusQuery(name="requests", promql="sum(requests_total)")],
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ctx = SimpleNamespace(
            service=Service(
                base_url="http://example",
                headers={"Authorization": "service-secret"},
            ),
            probe_client=client,
            t0=1.0,
        )
        await probe.sample(ctx)
    assert seen_headers["X-Metrics-Token"] == "metrics-secret"
    assert "Authorization" not in seen_headers


def test_observe_prometheus_wires_queries_and_slo_labels(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        _SERVICE + "observe:\n"
        "  - name: chat\n"
        "    probes:\n"
        "      - name: prometheus\n"
        "        queries:\n"
        "          - name: requests_by_path\n"
        "            promql: sum by (path) (requests_total)\n"
        "            kind: counter\n"
        "            unit: count\n"
        "            labels: [path]\n"
        "      - top\n"
        'slo: [ { metric: \'prometheus.requests_by_path{service="chat",path="/v1"}.rate\', lt: 5 } ]\n'
    )
    exp, _ = load_experiment(str(cfg))
    probe = next(p for p in exp.probes if p.name == "prometheus.chat")
    assert probe.queries[0].promql == "sum by (path) (requests_total)"
    assert probe.queries[0].labels == ("path",)
    assert exp.slo[0].metric == 'prometheus.requests_by_path{service="chat",path="/v1"}.rate'


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


async def test_prometheus_http_error_raises_not_empty():
    import httpx
    from prombed import PrombedError

    def handler(_request):
        return httpx.Response(500, content=b"boom")

    p = PrometheusProbe(
        service="chat",
        queries=[PrometheusQuery(name="requests", promql="sum(requests_total)")],
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ctx = SimpleNamespace(service=Service(base_url="http://x"), probe_client=client, t0=1.0)
        with pytest.raises(PrombedError, match="500"):
            await p.sample(ctx)


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


async def test_probe_errors_flow_to_trial_and_store():
    from perf_harness.metric import Missing
    from perf_harness.metric.store import MetricStore

    exp = Experiment(
        service=Service("m", base_url="http://127.0.0.1:0"),
        workload=_NoopWL(),
        resources=[ResourceProfile()],
        loads=[LoadProfile(model="closed", schedule=Schedule.ramp_hold(1, 0.0, 0.2))],
        probes=[_FlakyProbe()],
        observe_interval_s=0.05,
    )
    r = (await Engine(exp).run()).trials[0]
    pe = r.probe_errors["flaky.chat"]
    assert pe.failures == pe.ticks >= 1 and "down" in pe.last
    # an absent read on an errored probe is Missing(probe_error) — NOT "no slice"
    read = MetricStore([r]).query(r, 'flaky.x{service="chat"}.peak')
    assert isinstance(read, Missing) and read.reason == "probe_error"


def test_prometheus_query_replaces_derived_metrics(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        _SERVICE + "observe:\n"
        "  - name: chat\n"
        "    probes:\n"
        "      - name: prometheus\n"
        "        retention_ms: 120000\n"
        "        queries:\n"
        "          - name: x_mean_s\n"
        "            promql: sum(rate(x_seconds_sum[1m])) / sum(rate(x_seconds_count[1m]))\n"
        "            unit: s\n"
        "slo: [ { metric: 'prometheus.x_mean_s{service=\"chat\"}.mean', lt: 1 } ]\n"
    )
    exp, _ = load_experiment(str(cfg))
    probe = next(p for p in exp.probes if p.name == "prometheus.chat")
    assert probe._retention_ms == 120000
    assert probe.queries[0].promql.startswith("sum(rate(")
    assert exp.slo[0].metric == 'prometheus.x_mean_s{service="chat"}.mean'


def test_observe_prometheus_downstream_url_config(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        _SERVICE + "observe:\n"
        "  - name: worker\n"
        "    namespace: ns\n"
        "    k8s_selector: app=worker\n"
        "    probes:\n"
        "      - name: prometheus\n"
        "        url: http://worker:8000/metrics\n"
        "        queries: [ { name: tasks, promql: 'sum(tasks)' } ]\n"
    )
    exp, _ = load_experiment(str(cfg))
    probe = next(p for p in exp.probes if p.name == "prometheus.worker")
    assert probe._url == "http://worker:8000/metrics"


def test_top_level_derived_is_removed(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(_SERVICE + "derived: [ { as: bad, ratio: [nope, also_nope] } ]\n")
    with pytest.raises(ValueError, match="removed.*PromQL"):
        load_experiment(str(cfg))


async def test_up_series_synthesized_per_probe():
    # the Prometheus `up` analogue: health is a SERIES (when did it break), not just
    # the trial census. Healthy probe → all 1s; flaky probe → 0s and mean < 1.
    exp = Experiment(
        service=Service("m", base_url="http://127.0.0.1:0"),
        workload=_NoopWL(),
        resources=[ResourceProfile()],
        loads=[LoadProfile(model="closed", schedule=Schedule.ramp_hold(1, 0.0, 0.2))],
        probes=[_FanProbe(), _FlakyProbe()],
        observe_interval_s=0.05,
    )
    r = (await Engine(exp).run()).trials[0]
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
        "      - name: prometheus\n"
        "        queries: [ { name: up, promql: 'sum(x_total)' } ]\n"
    )
    with pytest.raises(ValueError, match="up"):
        load_experiment(str(cfg))


def test_up_is_slo_addressable(tmp_path):
    # observation availability can gate — explicitly, like everything observational
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        _SERVICE + "observe:\n  - { name: chat, probes: [top] }\n"
        "slo: [ { metric: 'top.up{service=\"chat\"}.mean', gte: 0.99 } ]\n"
    )
    exp, _ = load_experiment(str(cfg))
    assert exp.slo[0].metric == 'top.up{service="chat"}.mean'


def test_limits_colors_are_report_palette():
    # chart colors are report-side display policy, never model metadata: the limits
    # reference lines pin limit=red / request=orange; everything else rotates
    from perf_harness.report import family_color

    assert family_color("limits.cpu_limit") == "#d62728"
    assert family_color("limits.mem_limit") == "#d62728"
    assert family_color("limits.cpu_request") == "#ff7f0e"
    assert family_color("top.cpu_m") == ""
