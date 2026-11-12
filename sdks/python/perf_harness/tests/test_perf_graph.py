"""Topology is validated context; persisted observations stay usable without its source file."""

import json
from urllib.parse import parse_qs

import httpx
import pytest
import yaml
from harness_common import Graph, Node, NodeKind, Relation, RelationKind
from harness_toolbox.http import HTTPClient, HTTPClientProvider

from perf_harness import Engine, load_experiment, load_run, write_run
from perf_harness.metric.store import MetricStore
from perf_harness.report import write_report


def graph():
    return Graph(
        environment="qa",
        source="docs/calls.md",
        version="v1",
        nodes=(
            Node("service:chat", NodeKind.SERVICE),
            Node("service:search", NodeKind.SERVICE),
            Node("operation:chat/chat", NodeKind.OPERATION),
            Node("operation:search/retrieve", NodeKind.OPERATION),
        ),
        relations=(
            Relation("operation:chat/chat", "service:chat", RelationKind.BELONGS_TO),
            Relation("operation:search/retrieve", "service:search", RelationKind.BELONGS_TO),
            Relation("operation:chat/chat", "operation:search/retrieve", RelationKind.CALLS),
        ),
    )


def config(tmp_path):
    (tmp_path / "product.graph.json").write_text(json.dumps(graph().to_dict()))
    return {
        "prometheus": {"url": "http://prom", "step_s": 0.001},
        "graph": "product.graph.json",
        "service": {"name": "chat", "base_url": "http://chat", "environment": {"name": "qa"}},
        "runner": {"name": "mock"},
        "cases": [{"id": "one", "input": {"ms": 1}}],
        "load": {"request_rate": "inf", "max_inflight": 1, "hold_s": 0.01},
        "observe": [
            {
                "name": "search",
                "base_url": "http://search",
                "probes": [
                    {
                        "name": "prometheus_query",
                        "operation": "retrieve",
                        "queries": [
                            {"name": "inflight", "promql": "sum(active)"},
                        ],
                    }
                ],
            }
        ],
    }


def load(tmp_path, raw):
    path = tmp_path / "perf.yaml"
    path.write_text(yaml.safe_dump(raw))
    return load_experiment(str(path))[0]


async def test_graph_and_operation_metrics_survive_offline_reload(monkeypatch, tmp_path):
    raw = config(tmp_path)
    monkeypatch.chdir(tmp_path.parent)  # graph path belongs to config, not cwd
    experiment = load(tmp_path, raw)
    (tmp_path / "product.graph.json").unlink()

    def handler(request):
        start = float(parse_qs(request.content.decode())["start"][0])
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [{"metric": {}, "values": [[start, "3"]]}],
                },
            },
        )

    monkeypatch.setattr(
        HTTPClientProvider,
        "create_client",
        lambda provider, _: HTTPClient(transport=httpx.MockTransport(handler)),
    )
    run = await Engine(experiment, run_id="graph-test").run()
    assert run.status == "completed"
    paths = write_run(run, str(tmp_path / "runs"))
    restored = load_run(paths["run_dir"])
    assert restored.graph == graph()
    assert (
        restored.graph.reachable_from("operation:chat/chat", kind=RelationKind.CALLS)[-1].id
        == "operation:search/retrieve"
    )
    key = 'prometheus_query.inflight{service="search",operation="operation:search/retrieve"}.mean'
    arm = restored.arm_runs[0]
    assert MetricStore(restored.arm_runs).query(arm, key) == 3
    assert {key: value.samples for key, value in restored.arm_runs[0].series.items()} == {
        key: value.samples for key, value in run.arm_runs[0].series.items()
    }

    def offline(*args, **kwargs):
        raise AssertionError("report must not collect")

    monkeypatch.setattr(HTTPClientProvider, "create_client", offline)
    write_report(restored.arm_runs, str(tmp_path / "report"))


@pytest.mark.parametrize(
    "mistake", ["environment", "missing_operation", "owner", "observed_environment", "service"]
)
def test_invalid_binding_is_rejected_before_target_access(tmp_path, mistake):
    raw = config(tmp_path)
    if mistake == "environment":
        raw["service"]["environment"]["name"] = "dev"
    elif mistake == "observed_environment":
        raw["observe"][0]["environment"] = {"name": "dev"}
    elif mistake == "missing_operation":
        raw["observe"][0]["probes"][0]["operation"] = "typo"
    elif mistake == "service":
        raw["service"]["name"] = "absent"
    else:
        doc = graph().to_dict()
        for relation in doc["relations"]:
            if relation["source"] == "operation:search/retrieve":
                relation["target"] = "service:chat"
                relation["id"] = Relation(
                    relation["source"], "service:chat", RelationKind.BELONGS_TO
                ).id
        (tmp_path / "product.graph.json").write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="graph"):
        load(tmp_path, raw)


def test_operation_observation_does_not_require_graph(tmp_path):
    raw = config(tmp_path)
    del raw["graph"]
    experiment = load(tmp_path, raw)
    assert experiment.graph is None
    assert experiment.probes[-1].labels["operation"] == "operation:search/retrieve"
