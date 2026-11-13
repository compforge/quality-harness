import asyncio
from contextlib import asynccontextmanager

import pytest
from harness_toolbox.transport import Endpoint, PodRelayTransport

from perf_harness import Engine, Experiment, LoadPlan, Outcome, ResourceProfile, Runner, Service
from perf_harness.config import load_experiment
from perf_harness.drive.load import Warmup
from perf_harness.observe import FamilySpec, Probe

_CONFIG = """
service:
  name: chat
  base_url: http://10.0.0.10:8001
  environment: {name: qa, kind: kubernetes, kubeconfig: /tmp/config}
resources: [{}]
runner: {name: mock}
load: {request_rate: 8, max_inflight: 100, hold_s: 1}
request_transport:
  kind: pod-relay
  workload:
    name: relay
    namespace: quality
    location: {kind: service, name: utility}
  max_connections: 128
"""


@pytest.mark.parametrize("host_transport", [None, "", "local"])
def test_relay_config_is_pure_and_keeps_logical_service_url(tmp_path, host_transport):
    path = tmp_path / "config.yaml"
    config = _CONFIG
    if host_transport is not None:
        config = config.replace(
            "kubeconfig: /tmp/config",
            f'kubeconfig: /tmp/config, host: {{name: generator, transport: "{host_transport}"}}',
        )
    path.write_text(config)
    exp, _ = load_experiment(str(path))
    assert isinstance(exp.request_transport, PodRelayTransport)
    assert exp.request_transport.environment is exp.service.environment
    assert exp.request_transport.workload.namespace == "quality"
    assert exp.service.base_url == "http://10.0.0.10:8001"
    assert exp.request_transport.max_connections == 128


@pytest.mark.parametrize(
    "change, expected",
    [
        (("max_connections: 128", "max_connections: 0"), "positive"),
        (("kind: pod-relay", "kind: typo"), "request_transport.kind"),
        (("  max_connections: 128", "  unexpected: 1"), "unknown request_transport"),
        (("kind: kubernetes, kubeconfig: /tmp/config", "kind: generic"), "Kubernetes"),
        (("max_connections: 128", "startup_timeout_s: .inf"), "finite"),
    ],
)
def test_bad_relay_config_fails_before_io(tmp_path, change, expected):
    path = tmp_path / "config.yaml"
    path.write_text(_CONFIG.replace(*change))
    with pytest.raises(ValueError, match=expected):
        load_experiment(str(path))


def test_stage_peak_resolves_all_load_connection_pools(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        _CONFIG.replace(
            "hold_s: 1}",
            "hold_s: 1, stages: [{duration_s: 1, request_rate: 8, max_inflight: 150}]}",
        )
    )
    experiment, _ = load_experiment(str(path))
    assert experiment.request_transport.max_connections == 128
    assert experiment.effective_connection.max_connections == 150
    assert experiment.effective_connection.transport.max_connections == 150


@pytest.mark.parametrize("fail_at", [None, "setup", "fire", "cleanup"])
async def test_engine_uses_request_transport_through_cleanup_but_isolates_observers(fail_at):
    paths, events, sockets = [], [], set()

    async def serve(reader, writer):
        task = asyncio.current_task()
        sockets.add(task)
        try:
            while True:
                request = await reader.readuntil(b"\r\n\r\n")
                paths.append(request.split(b" ")[1].decode())
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 11\r\n\r\ndata: end\n\n")
                await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            sockets.discard(task)

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    class Route:
        key = client_key = "test-request-transport"

        def create_client(self, clients):
            return self

        async def initialize(self):
            events.append("initialize")

        async def dispose(self):
            events.append("dispose")

        @asynccontextmanager
        async def connect(self, target):
            assert target.host == "unresolvable.invalid"
            events.append("connect")
            try:
                yield Endpoint("127.0.0.1", port, target.host)
            finally:
                events.append("release")

    class HTTPRunner(Runner):
        async def call(self, ctx, phase):
            response = await ctx.client.get(ctx.service.base_url + "/" + phase)
            assert response.text == "data: end\n\n"
            if phase == fail_at:
                raise RuntimeError("failure in " + phase)
            return response

        async def setup(self, ctx):
            await self.call(ctx, "setup")

        async def fire(self, ctx):
            response = await self.call(ctx.arm_run, "fire")
            return Outcome(status=response.status_code, duration_ms=1)

        async def deactivate(self, ctx):
            await self.call(ctx, "deactivate")

        async def cleanup(self, ctx):
            await self.call(ctx, "cleanup")

    class Observer(Probe):
        name = "observer"
        families = {"count": FamilySpec("count", "gauge", "full")}

        async def sample(self, ctx):
            assert ctx.probe_client is not ctx.client
            response = await ctx.probe_client.get(f"http://127.0.0.1:{port}/observe")
            assert response.status_code == 200
            return {"count": 1}

    try:
        experiment = Experiment(
            service=Service("chat", base_url="http://unresolvable.invalid:8001"),
            runner=HTTPRunner(),
            request_transport=Route(),
            resources=[ResourceProfile()],
            probes=[Observer()],
            loads=[LoadPlan(warmup=Warmup(step_s=0), request_rate=20, max_inflight=2, hold_s=0.08)],
            observe_interval_s=0.01,
        )
        run = await Engine(experiment).run()
        assert "/setup" in paths and paths[-1] == "/cleanup"
        if fail_at != "setup":
            assert "/fire" in paths and "/observe" in paths
        assert not run.arm_runs[0].probe_errors
        if fail_at in {"setup", "cleanup"}:
            assert run.status == "error"
            assert run.arm_runs[0].phase_errors[0].phase == fail_at
        assert events[0] == "initialize" and events[-1] == "dispose"
        assert events.index("release") < events.index("dispose")
    finally:
        server.close()
        await server.wait_closed()
        await asyncio.gather(*sockets, return_exceptions=True)


async def test_failed_request_route_is_native_setup_error_without_dispatch():
    calls = []

    class Broken:
        client_key = "broken"

        def create_client(self, clients):
            return self

        async def initialize(self):
            raise ConnectionError("relay unavailable")

        async def dispose(self):
            calls.append("disposed")

    class UnreachableRunner(Runner):
        async def fire(self, ctx):
            pytest.fail("route initialization must finish before dispatch")

    run = await Engine(
        Experiment(
            service=Service("chat"),
            runner=UnreachableRunner(),
            request_transport=Broken(),
            resources=[ResourceProfile()],
            loads=[LoadPlan(request_rate=8, max_inflight=1, hold_s=0.01)],
        )
    ).run()
    assert run.status == "error" and calls == ["disposed"]
    arm = run.arm_runs[0]
    assert not arm.requests and not arm.operation_runs
    assert arm.phase_errors[0].phase == "setup"
    assert arm.phase_errors[0].message == "relay unavailable"
    assert not arm.full.complete


@pytest.mark.parametrize(
    "configured, peak, expected", [(99, 200, 200), (256, 200, 256), (99, 1, 128)]
)
def test_explicit_budget_is_a_floor_not_a_load_limit(tmp_path, configured, peak, expected):
    path = tmp_path / "config.yaml"
    path.write_text(
        _CONFIG.replace("max_connections: 128", f"max_connections: {configured}").replace(
            "max_inflight: 100", f"max_inflight: {peak}"
        )
    )
    experiment, _ = load_experiment(str(path))
    effective = experiment.effective_connection
    assert effective.configured_max_connections == configured
    assert effective.max_connections == expected
    assert effective.transport.max_connections == expected
    assert experiment.request_transport.max_connections == configured


async def test_effective_pool_reaches_engine_and_survives_reload(monkeypatch, tmp_path):
    from harness_toolbox.http import HTTPClient, HTTPClientProvider

    from perf_harness import load_run, write_run
    from perf_harness.drive.runner import MockRunner

    providers = []

    def create(provider, clients):
        providers.append(provider)
        return HTTPClient()

    monkeypatch.setattr(HTTPClientProvider, "create_client", create)
    experiment = Experiment(
        service=Service("test", base_url="http://unused"),
        runner=MockRunner(),
        resources=[ResourceProfile()],
        loads=[LoadPlan(request_rate=1, max_inflight=200, hold_s=0.01, warmup=Warmup(step_s=0))],
    )
    run = await Engine(experiment).run()
    assert next(p.max_connections for p in providers if p.pool == "load") == 200
    assert next(p.max_connections for p in providers if p.pool == "observe") == 8
    restored = load_run(write_run(run, str(tmp_path))["run_dir"])
    assert restored.connection_budget.effective_max_connections == 200
    assert restored.connection_budget.configured_max_connections is None
