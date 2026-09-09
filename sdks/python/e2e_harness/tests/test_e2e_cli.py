"""``e2e`` CLI — load case.yaml → run against a (mock-transport) SUT → verdict.json + exit code.

The CLI core (``run_files``) takes an injected runner, so the whole "engine as a usable tool"
path is tested without a live service: real JSONRunner over httpx MockTransport, real verdict
file written to disk.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from harness_common.verdict import CaseVerdict, build_run_verdict
from e2e_harness.cli import _build_config, _summary, build_parser, run_files
from e2e_harness.core.config import E2EConfig
from e2e_harness.runner.json_runner import JSONRunner

_CASES = Path(__file__).parent.parent / "examples" / "note_cases.yaml"


def _sut(request: httpx.Request) -> httpx.Response:
    if request.method == "POST" and request.url.path == "/note":
        body = json.loads(request.content or b"{}")
        if not body.get("name"):
            return httpx.Response(
                400, json={"error": "name required", "code": "InvalidArgument"}
            )
        return httpx.Response(201, json={"id": "nb_123", "name": body["name"]})
    return httpx.Response(404, json={})


def _runner() -> JSONRunner:
    return JSONRunner(
        E2EConfig(),
        client=httpx.Client(transport=httpx.MockTransport(_sut), base_url="http://sut"),
    )


def test_run_files_writes_verdict_json_and_passes(tmp_path):
    rv, path = run_files([str(_CASES)], _runner(), runs_dir=str(tmp_path), run_id="t1")
    assert rv.status == "pass" and rv.scope == "note"
    assert path == tmp_path / "note" / "t1" / "verdict.json"
    data = json.loads(path.read_text())
    # the wire carries the source-of-truth cases[], not a derived summary — a consumer folds
    # the counts itself (and rv.summary gives the same in-process).
    assert data["harness"] == "e2e" and "summary" not in data
    assert len(data["cases"]) == 2 and all(c["status"] == "pass" for c in data["cases"])
    assert (
        rv.summary["pass"] == 2 and rv.summary["total"] == 2
    )  # derived property still works


def test_run_files_reports_fail_for_a_wrong_contract(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "caseset: nb\n"
        "cases:\n"
        "  - id: wrong\n"
        "    input: { method: POST, path: /note, body: { name: x } }\n"
        "    judge: { e2e: { assert: [ { path: status, op: eq, value: 500 } ] } }\n"
    )
    rv, _ = run_files([str(bad)], _runner(), runs_dir=str(tmp_path), run_id="t2")
    assert rv.status == "fail"  # SUT returns 201, contract expects 500 → CI gate trips


def test_build_config_requires_base_url_and_strips_slash():
    with pytest.raises(SystemExit):
        _build_config(None, None)  # no config and no --base-url → fail fast
    assert _build_config(None, "http://x/").service.base_url == "http://x"


def test_summary_surfaces_failing_cases():
    rv = build_run_verdict(
        "e2e",
        "demo",
        "r1",
        [
            CaseVerdict("ok", "pass"),
            CaseVerdict("bad", "fail", reason="status eq 200: got 400"),
        ],
    )
    out = _summary(rv, Path("runs/demo/r1/verdict.json"))
    assert "FAIL" in out and "bad" in out and "got 400" in out


def test_parser_run_subcommand():
    args = build_parser().parse_args(
        ["run", "a.yaml", "b.yaml", "--base-url", "http://x", "--run-id", "r"]
    )
    assert args.cmd == "run" and args.cases == ["a.yaml", "b.yaml"]
    assert args.base_url == "http://x" and args.protocol == "json"


def test_run_files_rejects_mixed_caseset_identity(tmp_path):
    other = tmp_path / "other.yaml"
    other.write_text(
        "caseset: other\n"
        "cases:\n"
        "  - id: other\n"
        "    input: {path: /note}\n"
        "    judge: {e2e: {assert: [{path: status, op: eq, value: 200}]}}\n"
    )
    with pytest.raises(SystemExit, match="one canonical CaseSet"):
        run_files(
            [str(_CASES), str(other)],
            _runner(),
            runs_dir=str(tmp_path),
            run_id="mixed",
        )
