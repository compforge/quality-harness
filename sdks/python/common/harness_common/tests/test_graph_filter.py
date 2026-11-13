"""Reachability is computed after exclusion, with the original entry pinned."""

import json
import shutil
import subprocess
from importlib.resources import files

import pytest


def test_filter_preserves_only_reachable_nodes_without_mutating_metrics():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed to execute the browser graph filter")
    assets = files("harness_common.report_kit").joinpath("assets")
    frame = dict(
        entry_ids=["entry"],
        nodes=[
            dict(id="entry", group="front", diameter=112, label="QPS 2"),
            dict(id="sibling", group="front"),
            dict(id="middle", group="worker"),
            dict(id="leaf", group="model", diameter=160, label="QPS 20"),
            dict(id="alternate", group="backup"),
            dict(id="island", group="model"),
        ],
        edges=[
            dict(id="a", source="entry", target="middle"),
            dict(id="b", source="middle", target="leaf"),
            dict(id="c", source="entry", target="alternate"),
            dict(id="d", source="alternate", target="leaf"),
            dict(id="cycle", source="leaf", target="alternate"),
            dict(id="same-service", source="entry", target="sibling"),
        ],
    )
    script = """
const fs = require('fs'), vm = require('vm');
vm.runInThisContext(fs.readFileSync(process.argv[1] + '/graph-filter.js', 'utf8'));
const frame = JSON.parse(fs.readFileSync(0, 'utf8'));
const original = JSON.stringify(frame);
const filters = [['front', 'worker', 'model', 'backup'], ['front', 'model'],
  ['model', 'backup'], [], ['front', 'worker', 'model', 'backup']];
const results = filters.map(groups => rkFilterGraph(frame, new Set(groups)));
console.log(JSON.stringify({results, unchanged: original === JSON.stringify(frame)}));
"""
    result = subprocess.run(
        [node, "-e", script, str(assets)],
        input=json.dumps(frame),
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    data = json.loads(result.stdout)
    full, cut, alternate, none, restored = data["results"]
    assert data["unchanged"]
    assert {n["id"] for n in full["nodes"]} == {
        "entry",
        "sibling",
        "middle",
        "leaf",
        "alternate",
    }
    assert {n["id"] for n in cut["nodes"]} == {"entry", "sibling"}
    assert {n["id"] for n in alternate["nodes"]} == {"entry", "alternate", "leaf"}
    assert {e["id"] for e in alternate["edges"]} == {"c", "d", "cycle"}
    assert [n["id"] for n in none["nodes"]] == ["entry"]
    assert none["edges"] == []
    assert restored == full
    assert next(n for n in alternate["nodes"] if n["id"] == "leaf") == frame["nodes"][3]
