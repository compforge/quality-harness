"""Reusable declarations produce replayable RED evidence without graph-dependent collection."""

import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
import yaml
from harness_common import Graph, Node, NodeKind, Relation, RelationKind
from harness_toolbox.http import HTTPClient, HTTPClientProvider

from perf_harness import Engine, build_callgraph, load_experiment, load_run, write_run
from perf_harness.report.callgraph import section


def assets(tmp_path):
    graph = Graph(
        environment="qa",
        nodes=(
            Node("service:entry", NodeKind.SERVICE),
            Node(
                "operation:entry/search",
                NodeKind.OPERATION,
                properties={"method": "POST", "path": "/search", "response_mode": "sse"},
            ),
        ),
        relations=(Relation("operation:entry/search", "service:entry", RelationKind.BELONGS_TO),),
    )
    topology = json.loads(json.dumps(graph.to_dict()))
    del topology["environment"]  # reusable declaration binds into Common's environment snapshot
    (tmp_path / "product.graph.yaml").write_text(yaml.safe_dump(topology))
    observations = {
        "observations": [
            {
                "target": "operation:entry/search",
                "probes": [
                    {
                        "type": "prometheus_query",
                        "name": "traffic",
                        "data_source": "metrics",
                        "metrics": {
                            "qps": {
                                "promql": "rate(requests[$window])",
                                "unit": "rps",
                                "semantics": "completed",
                            },
                            "p95": {
                                "promql": "sum by (le) (increase(duration_bucket[$window]))",
                                "unit": "s",
                                "histogram": {"quantile": 0.95},
                            },
                        },
                    },
                    {
                        "type": "prometheus_query",
                        "name": "errors",
                        "data_source": "metrics",
                        "metrics": {
                            "error_rate": {
                                "promql": "errors / completed",
                                "unit": "ratio",
                                "semantics": "business failures / completed requests",
                            },
                        },
                    },
                ],
            }
        ]
    }
    (tmp_path / "observations.yaml").write_text(yaml.safe_dump(observations))
    raw = {
        "name": "red",
        "graph": "product.graph.yaml",
        "observation": "observations.yaml",
        "data_sources": {"metrics": {"type": "prometheus", "url": "http://metrics"}},
        "service": {"name": "entry", "environment": {"name": "qa"}, "base_url": "http://unused"},
        "runner": {"name": "mock"},
        "cases": [{"id": "search", "input": {"ms": 1}}],
        "load": {"request_rate": "inf", "max_inflight": 1, "hold_s": 0.01, "warmup": {"step_s": 0}},
        "observation_windows": [{"kind": "full"}, {"kind": "phase", "phase": "hold"}],
    }
    path = tmp_path / "experiment.yaml"
    path.write_text(yaml.safe_dump(raw))
    return path, raw, observations


@pytest.mark.parametrize("error_value", ["0", "0.125", None, "1.5", "failure"])
async def test_observation_to_red_offline(monkeypatch, tmp_path, error_value):
    path, raw, _ = assets(tmp_path)
    experiment, _ = load_experiment(str(path))
    assert experiment.observation[0].operation.response_mode == "sse"
    assert experiment.graph.environment == "qa"
    assert set(experiment.observation_bindings()[0].metrics) == {"qps", "p95", "error_rate"}
    seen = []

    def handler(request):
        params = parse_qs(request.content.decode())
        expression = params["query"][0]
        seen.append(expression)
        timestamp = float(params["time"][0])
        if expression == "errors / completed" and error_value == "failure":
            return httpx.Response(503)
        if "bucket" in expression:
            rows = [
                {"metric": {"le": le}, "value": [timestamp, count]}
                for le, count in [("1", "90"), ("+Inf", "100")]
            ]
        else:
            value = error_value if expression == "errors / completed" else "8"
            rows = [] if value is None else [{"metric": {}, "value": [timestamp, value]}]
        return httpx.Response(
            200, json={"status": "success", "data": {"resultType": "vector", "result": rows}}
        )

    monkeypatch.setattr(
        HTTPClientProvider,
        "create_client",
        lambda *_: HTTPClient(transport=httpx.MockTransport(handler)),
    )
    run = await Engine(experiment, run_id="red-test").run()
    assert run.status == "completed"
    paths = write_run(run, str(tmp_path / "runs"), config_path=str(path))
    restored = load_run(paths["run_dir"])
    assert restored.observation_bindings == run.observation_bindings
    (tmp_path / "observations.yaml").unlink()
    (tmp_path / "product.graph.yaml").unlink()

    def offline(*_):
        raise AssertionError("report must not query")

    monkeypatch.setattr(HTTPClientProvider, "create_client", offline)
    snapshot = build_callgraph(restored)
    props = snapshot.arms[0].windows[0].nodes[0].to_dict()["properties"]["perf"]
    assert props["metrics"]["qps"]["value"] == 8
    assert props["metrics"]["p95"]["value"] == "+Inf"
    errors = props["metrics"]["error_rate"]
    if error_value in {"0", "0.125"}:
        assert errors["value"] == float(error_value)
        assert errors["status"] == "observed"
        label = section(snapshot).blocks[-1].frames[0].nodes[0].label
        assert ("err 0.0%" if error_value == "0" else "err 12.5%") in label
    else:
        assert errors["value"] is None
        assert errors["status"] == ("missing" if error_value is None else "error")
    assert errors["semantics"] == "business failures / completed requests"
    assert errors["queries"][0]["expression"] == "errors / completed"
    assert "$window" not in " ".join(seen)
    # Snapshot contains every window; unselected cooldown has missing summaries, not zero.
    cooldown = next(w for w in snapshot.arms[0].windows if w.phase == "cooldown")
    assert cooldown.nodes[0].properties["perf"]["metrics"]["qps"]["value"] is None
    payload = json.loads(Path(paths["run_dir"], "run.json").read_text())
    assert payload["schema"] == 11


def test_graph_optional_and_service_workload_targets_reuse_common(tmp_path):
    path, raw, asset = assets(tmp_path)
    del raw["graph"]
    asset["operations"] = [
        {"name": "search", "service_name": "entry", "method": "POST", "path": "/search"}
    ]
    raw["service"]["workloads"] = [
        {
            "name": "worker",
            "namespace": "qa",
            "location": {"kind": "resource", "resource_kind": "Deployment", "name": "entry"},
        }
    ]
    asset["observations"].append(
        {
            "target": "service:entry",
            "workload": "worker",
            "probes": [
                {"type": "restart", "metrics": {"restarts": {"metric": "restarts.increase"}}}
            ],
        }
    )
    raw["observation"] = asset
    path.write_text(yaml.safe_dump(raw))
    experiment, _ = load_experiment(str(path))
    assert experiment.graph is None
    observed = experiment.observation[1]
    assert observed.workload is observed.service.workloads[0]
    binding = experiment.observation_bindings()[1]
    assert binding.target == "service:entry" and binding.workload == "worker"
    assert 'workload="worker"' in binding.metrics["restarts"].metric


@pytest.mark.parametrize(
    "mistake", ["target", "source", "unit", "duplicate", "workload", "unknown_field"]
)
def test_observation_errors_fail_before_load(tmp_path, mistake):
    path, raw, asset = assets(tmp_path)
    observation = asset["observations"][0]
    probe = observation["probes"][0]
    if mistake == "target":
        observation["target"] = "operation:entry/missing"
    elif mistake == "source":
        probe["data_source"] = "missing"
    elif mistake == "unit":
        probe["metrics"]["qps"]["unit"] = "s"
    elif mistake == "duplicate":
        duplicate = dict(probe, name="another")
        observation["probes"].append(duplicate)
    elif mistake == "workload":
        observation["workload"] = "missing"
    else:
        probe["unexpected"] = True
    raw["observation"] = asset
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError):
        load_experiment(str(path))


async def test_named_source_recollection_needs_no_project_assets(monkeypatch, tmp_path):
    from perf_harness.observe.history import recollect_metrics

    path, _, _ = assets(tmp_path)
    experiment, _ = load_experiment(str(path))
    run = await Engine(experiment, run_id="recollect").run(collect=False)
    paths = write_run(run, str(tmp_path / "runs"), config_path=str(path))
    original = Path(paths["run_dir"], "requests.jsonl").read_bytes()
    (tmp_path / "product.graph.yaml").unlink()
    (tmp_path / "observations.yaml").unlink()

    def handler(request):
        params = parse_qs(request.content.decode())
        timestamp = float(params["time"][0])
        rows = (
            [] if "bucket" in params["query"][0] else [{"metric": {}, "value": [timestamp, "0.1"]}]
        )
        return httpx.Response(
            200, json={"status": "success", "data": {"resultType": "vector", "result": rows}}
        )

    monkeypatch.setattr(
        HTTPClientProvider,
        "create_client",
        lambda *_: HTTPClient(transport=httpx.MockTransport(handler)),
    )
    recollected = await recollect_metrics(paths["run_dir"])
    assert Path(paths["run_dir"], "requests.jsonl").read_bytes() == original
    assert len(list(Path(paths["run_dir"], "metric-collections").glob("*.json"))) == 2
    errors = (
        build_callgraph(recollected)
        .arms[0]
        .windows[0]
        .nodes[0]
        .properties["perf"]["metrics"]["error_rate"]
    )
    assert errors["value"] == 0.1


def test_registered_producer_and_open_attributes_share_observation_contract(tmp_path):
    from perf_harness import PrometheusQueryProbe, WindowQuery, register_probe

    def factory(config):
        assert config.operation.id() == "operation:entry/search"
        assert config.prometheus.url == "http://metrics"
        return PrometheusQueryProbe(
            service=config.service.name,
            operation=config.operation,
            data_source=config.prometheus,
            summaries=[
                WindowQuery("amplification", "calls / entries", unit="ratio", window=w)
                for w in config.windows
            ],
        )

    register_probe("visit_ratio_test", factory)
    path, raw, asset = assets(tmp_path)
    asset["observations"][0]["probes"].append(
        {
            "type": "visit_ratio_test",
            "name": "visits",
            "data_source": "metrics",
            "metrics": {"visit_ratio": {"metric": "amplification.value"}},
        }
    )
    raw["observation"] = asset
    path.write_text(yaml.safe_dump(raw))
    experiment, _ = load_experiment(str(path))
    assert set(experiment.observation_bindings()[0].metrics) == {
        "qps",
        "p95",
        "error_rate",
        "visit_ratio",
    }


def test_overlapping_observation_windows_are_rejected(tmp_path):
    path, raw, _ = assets(tmp_path)
    raw["observation_windows"] = [{"kind": "phase"}, {"kind": "phase", "phase": "hold"}]
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="overlap"):
        load_experiment(str(path))


def test_project_observation_example_resolves_without_target_access():
    path = Path(__file__).parents[1] / "examples/observation/experiment.yaml"
    experiment, _ = load_experiment(str(path), mock=True)
    assert experiment.graph.environment == "local"
    binding = experiment.observation_bindings()[0]
    assert binding.target == "operation:search/search"
    assert set(binding.metrics) == {"qps", "p95", "error_rate"}
