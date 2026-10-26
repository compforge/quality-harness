"""Cross-language PR identity and optional fact conformance."""

from dataclasses import asdict, replace
import json
from pathlib import Path

from harness_common import Forge, Identity, PullRequest, Repository


def _repository(data):
    return Repository(
        Forge(**data["forge"]), data["path"], forked_from=data.get("forked_from")
    )


def _fixtures():
    path = Path(__file__).resolve().parents[5] / "conformance/common/pull-requests.json"
    rows = json.loads(path.read_text())
    prs = []
    for row in rows:
        data = dict(row["pull_request"])
        data["repository"] = _repository(data["repository"])
        if "source_repository" in data:
            data["source_repository"] = _repository(data["source_repository"])
        prs.append(PullRequest(**data))
    return rows, prs


def test_pull_request_identity_ignores_fork_state_and_revision():
    rows, prs = _fixtures()
    for row, pr in zip(rows, prs, strict=True):
        identity: Identity = pr
        assert identity.id() == row["id"]
    assert len(set(prs[:4])) == 1
    assert len(set(prs)) == 4
    assert prs[0].source_repository != prs[0].repository
    assert replace(prs[0], head_sha="force-pushed", state="open") == prs[1]


def test_unknown_facts_are_not_inferred_and_wire_facts_roundtrip():
    rows, prs = _fixtures()

    def omit_unknown(value):
        if isinstance(value, dict):
            return {
                key: omit_unknown(item)
                for key, item in value.items()
                if item is not None
            }
        return value

    for row, pr in zip(rows, prs, strict=True):
        assert omit_unknown(asdict(pr)) == row["pull_request"]
    assert prs[3].state is None
    assert prs[3].source_repository is None
    assert prs[2].merged_at is None
    assert prs[1].state == "merged"
