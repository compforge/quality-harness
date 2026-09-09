import pytest
from pydantic import ValidationError
from spec_case.model import Case

from eval_harness.model.evalset import EvalSet, FacetSchema, FacetSpec, eval_view
from eval_harness.model.experiment import Arm, Experiment, Service, expand_matrix
from eval_harness.model.sample import MetricResult
from eval_harness.tests.eval_cases import make_eval_case


def test_metric_result_channel():
    assert MetricResult("correctness", "score", score=0.8).channel == "quality"
    assert MetricResult("latency", "measure", value=1200, unit="ms").channel == "measurement"
    rt = MetricResult.from_dict(MetricResult("x", "binary", score=1.0, judgement="hi").to_dict())
    assert rt.score == 1.0 and rt.judgement == "hi" and rt.kind == "binary"


def test_eval_view_contract():
    # eval's read of a canonical case enforces eval's contract (the schema is common.Case;
    # the per-face rules live in eval_view, not the case model).
    eval_view(make_eval_case(id="a", expected_behavior="answer", ground_truth="42"))
    with pytest.raises(ValueError, match="refuse"):  # refuse case must not carry ground_truth
        eval_view(
            Case(
                id="b",
                input={"query": "q"},
                judge={"eval": {"expected_behavior": "refuse", "ground_truth": "nope"}},
            )
        )
    with pytest.raises(ValueError, match="query"):  # no stimulus → not an eval case
        eval_view(Case(id="c", input={}))


def test_facet_schema_validation():
    schema = FacetSchema(
        {
            "difficulty": FacetSpec(values=["easy", "medium", "hard"], ordered=True),
            "type": FacetSpec(values=["factual", "summary"]),
            "domain": FacetSpec(open=True),
        }
    )
    # difficulty order comes from the CaseSet vocabulary.
    schema.validate_dimensions(
        "c1", {"difficulty": "hard", "type": "summary", "domain": "anything"}
    )
    with pytest.raises(ValueError):  # unknown facet key
        schema.validate_dimensions("c2", {"topic": "x"})
    with pytest.raises(ValueError):  # constrained value out of vocab
        schema.validate_dimensions("c3", {"difficulty": "trivial"})
    # ordered facet sorts by declared order, not alpha
    assert schema.order_key("difficulty", "easy") < schema.order_key("difficulty", "hard")


def test_facet_spec_requires_values_or_open():
    with pytest.raises(ValidationError):
        FacetSpec()


def _target(tenant="t1"):
    return Service(name="chat", config={"tenant_id": tenant, "host": {"base_url": "http://x"}})


# which config paths re-provision (the consumer declares these per experiment)
_HEAVY = ["config.tenant_id"]


def test_arm_resolve_and_dotted_override():
    arm = Arm(
        id="e",
        overrides={
            "config.params.target_searches": 6,
            "config.host.base_url": "http://y",
        },
    )
    r = arm.resolve(_target())
    assert r.config["params"]["target_searches"] == 6
    assert r.config["host"]["base_url"] == "http://y"
    assert r.config["tenant_id"] == "t1"


def test_arm_key_light_vs_heavy():
    t = _target()
    base = Arm(id="base", overrides={})
    light = Arm(id="light", overrides={"llm.model": "model-beta"})  # light → same key
    heavy = Arm(id="heavy", overrides={"config.tenant_id": "t2"})  # heavy → different key
    assert base.key("rag", t, _HEAVY) == light.key("rag", t, _HEAVY)
    assert base.key("rag", t, _HEAVY) != heavy.key("rag", t, _HEAVY)
    assert base.key("rag", t, _HEAVY) != base.key("other_corpus", t, _HEAVY)  # corpus is heavy


def test_expand_matrix():
    arms = expand_matrix({"config.params.target_searches": [3, 6], "config.tenant_id": ["a", "b"]})
    assert len(arms) == 4
    names = {arm.id for arm in arms}
    assert "target_searches=3__tenant_id=a" in names


def _exp(arms=None, matrix=None):
    return Experiment(
        name="exp",
        service=_target(),
        evalsets=[
            EvalSet(
                caseset="rag",
                cases=[make_eval_case(id="q1", query="q", ground_truth="a")],
            )
        ],
        arms=arms or [],
        matrix=matrix or {},
        metrics=["correctness"],
        weights={"correctness": 1.0},
    )


def test_resolved_arms_defaults_to_one():
    arms = _exp().resolved_arms()
    assert len(arms) == 1 and arms[0].id == "default" and arms[0].overrides == {}


def test_arm_ids_must_be_unique():
    with pytest.raises(ValueError, match="duplicate arm id"):
        _exp(arms=[Arm(id="same"), Arm(id="same")])


def test_experiment_hash_stable_and_sensitive():
    e1 = _exp(arms=[Arm(id="a"), Arm(id="b")])
    e2 = _exp(arms=[Arm(id="b"), Arm(id="a")])  # order-independent
    assert e1.experiment_hash() == e2.experiment_hash()
    e3 = _exp(arms=[Arm(id="a"), Arm(id="c")])  # different arm_id set
    assert e1.experiment_hash() != e3.experiment_hash()
