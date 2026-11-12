"""Remote history is environment-configured and never collected by the load loop."""

import pytest
import yaml

from perf_harness.config import load_experiment
from perf_harness.observe import PrometheusQuery, PrometheusQueryProbe
from perf_harness.observe.configuration import parse_prometheus


def config():
    return {
        "service": {"name": "api", "base_url": "http://api"},
        "runner": {"name": "mock"},
        "load": {"request_rate": 1, "max_inflight": 1, "hold_s": 1},
        "prometheus": {"url": "http://prom/proxy", "timeout_ms": 1000, "step_s": 5},
        "observe": [
            {
                "name": "api",
                "probes": [
                    {
                        "name": "prometheus_query",
                        "operation": "ask",
                        "queries": [
                            {
                                "name": "rps",
                                "promql": "sum(rate(requests_total[1m]))",
                                "unit": "rps",
                            }
                        ],
                        "summaries": [
                            {"name": "count", "promql": "sum(increase(requests_total[$window]))"}
                        ],
                    }
                ],
            }
        ],
    }


def test_yaml_source_is_shared_with_registered_probes_and_keeps_secrets_out(tmp_path, monkeypatch):
    from perf_harness.observe import register_probe

    monkeypatch.setenv("PERF_PROM_AUTH", "Bearer secret")
    raw = config()
    raw["prometheus"]["headers_env"] = {"Authorization": "PERF_PROM_AUTH"}
    received = []

    def factory(config):
        received.append(config)
        return PrometheusQueryProbe(
            data_source=config.prometheus,
            step_s=config.step_s,
            service=config.service.name,
            summaries=[],
            queries=[PrometheusQuery("other", "sum(up)")],
        )

    register_probe("test_remote_custom", factory)
    raw["observe"].append({"name": "worker", "probes": ["test_remote_custom"]})
    path = tmp_path / "perf.yaml"
    path.write_text(yaml.safe_dump(raw))
    exp, _ = load_experiment(str(path))
    remote = exp.probes[1]
    assert remote.data_source.url == "http://prom/proxy" and remote.step_s == 5
    assert remote.data_source.options.timeout_ms == 1000
    assert remote.data_source.headers == {"Authorization": "Bearer secret"}
    assert remote.data_source is received[0].prometheus
    assert not remote.periodic and "secret" not in repr(remote.data_source)
    assert "secret" not in path.read_text()


@pytest.mark.parametrize(
    "change,match",
    [
        (lambda c: c.pop("prometheus"), "prometheus.url"),
        (lambda c: c["prometheus"].update(step_s=0), "step_s"),
        (lambda c: c["prometheus"].update(timeout_ms=True), "positive integers"),
        (
            lambda c: c["prometheus"].update(headers={"Authorization": "secret"}),
            "supported query options",
        ),
        (lambda c: c["observe"][0]["probes"][0].update(name="metric"), "no longer scrapes"),
        (
            lambda c: c["observe"][0]["probes"][0].update(transport="port-forward"),
            "unknown options",
        ),
        (
            lambda c: c["observe"][0]["probes"][0].update(queries=[{"name": "up", "promql": "up"}]),
            "reserved",
        ),
    ],
)
def test_removed_or_invalid_source_configuration_fails_before_load(tmp_path, change, match):
    raw = config()
    change(raw)
    path = tmp_path / "perf.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match=match):
        load_experiment(str(path))


def test_missing_auth_environment_fails_without_echoing_credentials(monkeypatch):
    monkeypatch.delenv("ABSENT_PROMETHEUS_AUTH", raising=False)
    with pytest.raises(ValueError, match="ABSENT_PROMETHEUS_AUTH"):
        parse_prometheus(
            {"url": "http://prom", "headers_env": {"Authorization": "ABSENT_PROMETHEUS_AUTH"}}
        )
