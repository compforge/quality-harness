import pytest

from perf_harness.config import load_experiment
from perf_harness.deploy import HelmDeployer
from perf_harness.drive.runner import MockRunner
from perf_harness.engine import Engine

_MIX = """
service: { name: chat, base_url: "http://x:8001" }
resources: [ { workers: 2 } ]
runner: { name: mock }
facets: { difficulty: { values: [simple, complex], ordered: true } }
cases:
  - { id: a, weight: 70, facets: {difficulty: simple}, input: {ms: 3} }
  - { id: b, weight: 30, facets: {difficulty: complex}, input: {ms: 25} }
load: { request_rate: inf, max_concurrency: 2, duration_s: 1 }
output_dir: /tmp/x
"""

_SINGLE = """
service: { name: chat, base_url: "http://x" }
resources: [ {} ]
runner: { name: mock }
payload: { ms: 5 }
load: { request_rate: inf, max_concurrency: 1, duration_s: 0.5 }
"""

_BREAKER = """
service: { name: chat, base_url: "http://x" }
resources: [ {} ]
runner: { name: mock }
load: { request_rate: inf, max_concurrency: 2, duration_s: 1, abort_on_error_rate: 0.1, breaker_min_n: 5 }
"""


def test_config_parses_circuit_breaker(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(_BREAKER)
    exp, _ = load_experiment(str(cfg))
    # both load arms carry the mid-arm_run circuit-breaker config
    assert all(ld.abort_on_error_rate == 0.1 and ld.breaker_min_n == 5 for ld in exp.loads)
    assert all(ld.drain_timeout_s == 30.0 for ld in exp.loads)  # default drain window


def test_config_builds_helm_deployer_from_common_deployment_model(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "service:\n"
        "  name: chat\n"
        "  component:\n"
        "    repository: {forge: github, path: example/chat}\n"
        "    name: api\n"
        "  environment: {name: dev, kubeconfig: /tmp/kubeconfig}\n"
        "  namespace: default\n"
        "  base_url: http://chat\n"
        "deployer: {type: helm, release: chat, chart_path: ./chart}\n"
        "resources: [{}]\n"
        "runner: {name: mock}\n"
        "load: {request_rate: inf, max_concurrency: 1, duration_s: 0.1}\n"
    )

    experiment, _ = load_experiment(str(cfg), mock=True)

    assert isinstance(experiment.deployer, HelmDeployer)
    assert experiment.service.component.repository.forge.name == "github"
    assert experiment.service.component.repository.path == "example/chat"


@pytest.mark.parametrize(
    "bad",
    [
        "abort_on_error_rate: 0",  # would trip healthy traffic at min_n
        "abort_on_error_rate: 1.5",  # never trips
        "breaker_min_n: 0",  # no statistical floor
        "drain_timeout_s: -1",  # bypasses drain, not an explicit hard stop
    ],
)
def test_config_rejects_bad_stop_policy(tmp_path, bad):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "service: { name: s, base_url: 'http://x' }\n"
        "resources: [ {} ]\nrunner: { name: mock }\n"
        f"load: {{ request_rate: inf, max_concurrency: 1, duration_s: 0.1, {bad} }}\n"
    )
    with pytest.raises(ValueError):
        load_experiment(str(cfg))


def test_config_rejects_unsupported_max_requests(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "service: { name: s, base_url: 'http://x' }\n"
        "resources: [ {} ]\nrunner: { name: mock }\n"
        "load: { request_rate: inf, max_concurrency: 1, duration_s: 0.1, max_requests: 10 }\n"
    )
    with pytest.raises(ValueError, match="unknown load fields"):
        load_experiment(str(cfg))


def test_load_experiment_parses_cases_and_facets(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(_MIX)
    experiment, out = load_experiment(str(cfg))

    assert out == "/tmp/x"
    assert experiment.name == "chat"  # config has no `name:` → service slug
    assert experiment.runner.name == "mock"
    assert len(experiment.loads) == 1  # two levels
    assert {c.id for c in experiment.cases} == {"a", "b"}
    assert experiment.cases[0].facets == {"difficulty": "simple"}
    # weight rides the config ENTRY (experiment usage) — the Case object stays clean
    assert not hasattr(experiment.cases[0], "weight")
    assert experiment.mix.overrides == {"a": 70.0, "b": 30.0}
    assert experiment.facet_order == {"difficulty": ["simple", "complex"]}


def test_load_experiment_references_canonical_caseset(tmp_path):
    (tmp_path / "cases.yaml").write_text(
        "caseset: shared-perf\n"
        "facets:\n"
        "  difficulty: { values: [simple, complex], ordered: true }\n"
        "cases:\n"
        "  - id: simple\n"
        "    desc: quick request\n"
        "    input: {ms: 3}\n"
        "    facets: {difficulty: simple}\n"
        "    judge: {perf: {p99_ms: 20}}\n"
        "    binding: {symbol_id: internal/api.py::run, spec_id: sync_run}\n"
        "  - id: complex\n"
        "    input: {ms: 25}\n"
        "    facets: {difficulty: complex}\n"
    )
    cfg = tmp_path / "experiment.yaml"
    cfg.write_text(
        _BASE + "caseset: ./cases.yaml\n"
        "cases:\n"
        "  - {id: complex, weight: 30}\n"
        "  - {id: simple, weight: 70}\n"
        "load: { request_rate: inf, max_concurrency: 1, duration_s: 0.1 }\n"
    )

    experiment, _ = load_experiment(str(cfg))

    assert [case.id for case in experiment.cases] == ["complex", "simple"]
    assert experiment.mix.overrides == {"complex": 30.0, "simple": 70.0}
    assert experiment.facet_order == {"difficulty": ["simple", "complex"]}
    simple = experiment.cases[1]
    assert simple.desc == "quick request"
    assert simple.judge == {"perf": {"p99_ms": 20}}
    assert simple.binding.symbol_id == "internal/api.py::run"
    assert simple.binding.spec_id == "sync_run"


def test_canonical_caseset_selection_does_not_override_case_data(tmp_path):
    (tmp_path / "cases.yaml").write_text(
        "caseset: shared-perf\ncases:\n  - {id: simple, input: {ms: 3}}\n"
    )
    cfg = tmp_path / "experiment.yaml"
    cfg.write_text(
        _BASE + "caseset: ./cases.yaml\n"
        "cases:\n"
        "  - {id: simple, request_json: {ms: 9}}\n"
        "load: { request_rate: inf, max_concurrency: 1, duration_s: 0.1 }\n"
    )

    with pytest.raises(ValueError, match="only `id`.*`weight`"):
        load_experiment(str(cfg))


def test_canonical_caseset_fails_fast_on_unknown_selection(tmp_path):
    (tmp_path / "cases.yaml").write_text(
        "caseset: shared-perf\ncases:\n  - {id: simple, input: {ms: 3}}\n"
    )
    cfg = tmp_path / "experiment.yaml"
    cfg.write_text(
        _BASE + "caseset: ./cases.yaml\n"
        "cases: [{id: missing}]\n"
        "load: { request_rate: inf, max_concurrency: 1, duration_s: 0.1 }\n"
    )

    with pytest.raises(ValueError, match="not found"):
        load_experiment(str(cfg))


def test_mix_resolves_to_engine_weights(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(_MIX)
    experiment, _ = load_experiment(str(cfg))
    eng = Engine(experiment)
    # _weights are positional to experiment.cases (a, b); the mix sets both, no default needed
    assert eng._weights == [70.0, 30.0]


def test_load_experiment_backcompat_single_payload(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(_SINGLE)
    experiment, _ = load_experiment(str(cfg))

    assert len(experiment.cases) == 1
    assert experiment.cases[0].id == "default"
    assert experiment.cases[0].input == {"ms": 5}
    assert experiment.facet_order == {}


_BASE = """
service: { name: s, base_url: "http://x" }
resources: [ {} ]
runner: { name: mock }
"""


def _write(tmp_path, load_block: str):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(_BASE + load_block)
    return str(cfg)


def test_invalid_model_fails_fast(tmp_path):
    with pytest.raises(ValueError, match="unknown load fields"):
        load_experiment(_write(tmp_path, "load: { model: opn, levels: [1], steady_s: 0.1 }"))


def test_invalid_pacing_kind_fails_fast(tmp_path):
    with pytest.raises(ValueError, match="unknown load fields"):
        load_experiment(
            _write(
                tmp_path,
                "load: { model: closed, levels: [1], pacing: { kind: paced } }",
            )
        )


def test_stages_and_levels_mutually_exclusive(tmp_path):
    block = "load: { model: open, levels: [1], stages: [ { hold: 1, for_s: 1 } ] }"
    with pytest.raises(ValueError, match="unknown load fields"):
        load_experiment(_write(tmp_path, block))


def test_all_zero_weights_fail_fast(tmp_path):
    extra = (
        "cases:\n  - { id: a, weight: 0 }\n  - { id: b, weight: 0 }\n"
        "load: { request_rate: inf, max_concurrency: 1, duration_s: 0.1 }\n"
    )
    with pytest.raises(ValueError, match="every case at 0"):
        load_experiment(_write(tmp_path, extra))


def test_top_level_mix_is_a_migration_error(tmp_path):
    # the old top-level mix: must point at inline weight, not silently parse
    extra = (
        "cases:\n  - { id: a }\nmix: { a: 2 }\n"
        "load: { request_rate: inf, max_concurrency: 1, duration_s: 0.1 }\n"
    )
    with pytest.raises(ValueError, match="`weight:`"):
        load_experiment(_write(tmp_path, extra))


def test_slo_facet_label_typo_fails_fast(tmp_path):
    extra = (
        "cases:\n  - { id: a, facets: {difficulty: simple} }\n"
        "slo: [ { metric: 'p99_ms{difficulty=\"complx\"}', lt: 1 } ]\n"
        "load: { request_rate: 1, max_concurrency: 128, duration_s: 0.1 }\n"
    )
    with pytest.raises(ValueError, match="facet difficulty=complx unknown"):
        load_experiment(_write(tmp_path, extra))


def test_slo_window_name_typo_fails_fast(tmp_path):
    extra = (
        "slo: [ { metric: p99_ms, window: {kind: hold, name: 'hold@99'}, lt: 1 } ]\n"
        "load:\n  request_rate: 10\n  max_concurrency: 32\n  duration_s: 0.2\n  stages:\n"
        "    - {request_rate: 10, max_concurrency: 32, duration_s: 0.1}\n    - {request_rate: 20, max_concurrency: 32, duration_s: 0.1}\n"
    )
    with pytest.raises(ValueError, match="matches no configured stage"):
        load_experiment(_write(tmp_path, extra))


def test_slo_legacy_scope_key_rejected(tmp_path):
    extra = (
        'slo: [ { metric: p99_ms, lt: 1, scope: "overall" } ]\n'
        "load: { request_rate: 1, max_concurrency: 128, duration_s: 0.1 }\n"
    )
    with pytest.raises(ValueError, match="slo.scope was removed"):
        load_experiment(_write(tmp_path, extra))


_UNREGISTERED = """
service: { name: s, base_url: "http://x" }
resources: [ {} ]
runner: { name: chat }
load: { request_rate: inf, max_concurrency: 1, duration_s: 0.1 }
"""


def test_mock_bypasses_unregistered_runner(tmp_path):
    cfg = tmp_path / "u.yaml"
    cfg.write_text(_UNREGISTERED)
    with pytest.raises(ValueError, match="unknown runner"):
        load_experiment(str(cfg))  # real path needs `chat` registered
    exp, _ = load_experiment(str(cfg), mock=True)  # mock skips the registry
    assert isinstance(exp.runner, MockRunner)


def test_extension_module_registers_runner_and_probe(tmp_path, monkeypatch):
    module = tmp_path / "perf_consumer_ext.py"
    module.write_text(
        "from perf_harness import (\n"
        "    FamilySpec, Outcome, Probe, Runner, register_probe, register_runner\n"
        ")\n"
        "class ExtensionRunner(Runner):\n"
        "    name = 'extension-runner'\n"
        "    async def fire(self, ctx):\n"
        "        return Outcome(status=200, duration_ms=1.0)\n"
        "class ExtensionProbe(Probe):\n"
        "    name = 'extension-probe'\n"
        "    source = 'test'\n"
        "    families = {'value': FamilySpec('count')}\n"
        "    def __init__(self, cfg):\n"
        "        self._service = cfg.service.name\n"
        "        self.name = f'{self.name}.{cfg.service.name}'\n"
        "        self.answer = cfg.options['answer']\n"
        "    async def sample(self, ctx):\n"
        "        return {'value': float(self.answer)}\n"
        "register_runner('extension-runner', lambda cfg: ExtensionRunner())\n"
        "register_probe('extension-probe', ExtensionProbe)\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "extensions: [perf_consumer_ext]\n"
        "service: { name: s, base_url: 'http://x' }\n"
        "resources: [ {} ]\n"
        "runner: { name: extension-runner }\n"
        "load: { request_rate: inf, max_concurrency: 1, duration_s: 0.1 }\n"
        "observe:\n"
        "  - name: s\n"
        "    probes: [ { name: extension-probe, answer: 42 } ]\n"
    )
    exp, _ = load_experiment(str(cfg))
    assert exp.runner.name == "extension-runner"
    probe = next(p for p in exp.probes if p.name == "extension-probe.s")
    assert probe.answer == 42 and probe.labels == {"service": "s"}


def test_cooldown_parses_and_rejects_negative(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(_SINGLE + "cooldown_s: 12\n")
    exp, _ = load_experiment(str(cfg))
    assert exp.cooldown_s == 12
    cfg.write_text(_SINGLE + "cooldown_s: -1\n")
    with pytest.raises(ValueError, match="cooldown_s"):
        load_experiment(str(cfg))


def test_slo_multi_facet_label_rejected(tmp_path):
    # marginal pivot, not a cube → at most one facet slice label
    extra = (
        "cases:\n  - { id: a, facets: {difficulty: simple, lang: zh} }\n"
        'slo: [ { metric: \'p99_ms{difficulty="simple",lang="zh"}\', lt: 1 } ]\n'
        "load: { request_rate: 1, max_concurrency: 128, duration_s: 0.1 }\n"
    )
    with pytest.raises(ValueError, match="at most one facet slice"):
        load_experiment(_write(tmp_path, extra))


def test_slo_undeclared_per_request_metric_rejected(tmp_path):
    # a dynamic first_<event>_ms reaches the report but cannot gate (fail-fast)
    extra = (
        "slo: [ { metric: first_answer_ms.p95, lt: 1 } ]\n"
        "load: { request_rate: 1, max_concurrency: 128, duration_s: 0.1 }\n"
    )
    with pytest.raises(ValueError, match="not a declared metric"):
        load_experiment(_write(tmp_path, extra))


def test_slo_framework_ttft_is_declared(tmp_path):
    extra = (
        "slo: [ { metric: first_byte_ms.p95, lt: 2000 } ]\n"
        "load: { request_rate: 1, max_concurrency: 128, duration_s: 0.1 }\n"
    )
    exp, _ = load_experiment(_write(tmp_path, extra))  # first_byte_ms is framework-declared
    assert exp.slo[0].metric == "first_byte_ms.p95"


def test_named_judge_and_common_workload(tmp_path):
    from harness_common import KubernetesWorkload

    from perf_harness import RequestEvaluation, register_judge

    def judge(outcome):
        return RequestEvaluation(False, "business")

    register_judge("fixture-business", judge)
    path = _write(
        tmp_path,
        "judge: fixture-business\n"
        "service:\n  name: mock\n  base_url: http://mock\n  workloads:\n"
        "    - name: api\n      namespace: test\n"
        "      location: {kind: resource, resource_kind: Deployment, name: chat}\n"
        "load: {request_rate: 4, max_concurrency: 32, duration_s: 60}\n",
    )
    experiment, _ = load_experiment(path)
    assert experiment.judge is judge
    assert isinstance(experiment.service.workloads[0], KubernetesWorkload)
