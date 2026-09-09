import pytest
import yaml

from eval_harness.config import load_experiment
from eval_harness.model.experiment import Arm, LLMSpec, Service, expand_matrix
from eval_harness.schedule.reconcile import _sut_endpoint

_HEAVY = ["config.tenant_id"]


def _target(llm=None):
    return Service(
        name="chat",
        config={"tenant_id": "t1"},
        llm=llm or LLMSpec(base_url="http://aigw", api_key="K", model="m0"),
    )


def test_llm_override_patches_and_inherits():
    r = Arm(id="model-alpha", overrides={"llm.model": "model-alpha-pro"}).resolve(_target())
    assert r.llm.model == "model-alpha-pro"
    assert r.llm.base_url == "http://aigw"  # inherited from base
    assert r.llm.api_key == "K"


def test_llm_override_when_base_llm_is_none():
    r = Arm(id="x", overrides={"llm.model": "m1"}).resolve(
        Service(name="chat", config={"tenant_id": "t1"})
    )
    assert r.llm.model == "m1"


def test_llm_is_light_not_in_arm_key():
    t = _target()
    base = Arm(id="b").key("rag", t, _HEAVY)
    swap = Arm(id="s", overrides={"llm.model": "model-beta"}).key("rag", t, _HEAVY)
    assert base == swap  # swapping model keeps the key → shared prepare
    heavy = Arm(id="h", overrides={"config.tenant_id": "t2"}).key("rag", t, _HEAVY)
    assert base != heavy


def test_matrix_sweeps_llm_model():
    arms = expand_matrix({"llm.model": ["model-alpha", "model-beta"]})
    assert {arm.id for arm in arms} == {"model=model-alpha", "model=model-beta"}
    assert {"llm.model": "model-alpha"} in [e.overrides for e in arms]


def test_sut_endpoint_from_llm():
    a = _sut_endpoint(_target())
    b = _sut_endpoint(_target(LLMSpec(base_url="http://aigw", model="model-beta")))
    assert a == "http://aigw::m0"
    assert a != b  # different model → different rate bucket


# ---- ${ENV} interpolation in load_experiment ----

_BODY = {
    "name": "exp",
    "evalset": "cases.yaml",
}


def _write(tmp_path, service):
    d = tmp_path / "experiments"
    d.mkdir()
    (tmp_path / "cases.yaml").write_text(
        "caseset: c\ncases:\n"
        "  - {id: q1, input: {query: q}, judge: {eval: {ground_truth: a}}}\n",
        encoding="utf-8",
    )
    body = {**_BODY, "service": service}
    f = d / "exp.yaml"
    f.write_text(yaml.safe_dump(body, allow_unicode=True), encoding="utf-8")
    return f


def test_interpolation_resolves_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MYKEY", "secret123")
    service = {"name": "chat", "llm": {"model": "m", "api_key": "${MYKEY}"}}
    exp = load_experiment(_write(tmp_path, service))
    assert exp.service.llm.api_key == "secret123"


def test_interpolation_default(tmp_path, monkeypatch):
    monkeypatch.delenv("HOSTX", raising=False)
    service = {
        "name": "chat",
        "config": {"host": {"base_url": "${HOSTX:-http://default}"}},
    }
    exp = load_experiment(_write(tmp_path, service))
    assert exp.service.config["host"]["base_url"] == "http://default"


def test_interpolation_missing_fails_loud(tmp_path, monkeypatch):
    monkeypatch.delenv("NOPE", raising=False)
    service = {"name": "chat", "config": {"host": {"base_url": "${NOPE}"}}}
    with pytest.raises(ValueError, match="unset env var"):
        load_experiment(_write(tmp_path, service))
