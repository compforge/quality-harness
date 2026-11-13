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
            dict(id="middle", group="worker", importance="low"),
            dict(
                id="leaf",
                group="model",
                diameter=160,
                label="QPS 20",
                importance="high",
            ),
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
const all = new Set(filters[0]);
const high = rkFilterGraph(frame, all, new Set(['high']));
const highMedium = rkFilterGraph(frame, all, new Set(['high', 'medium']));
const highLow = rkFilterGraph(frame, all, new Set(['high', 'low']));
const intersect = rkFilterGraph(frame, new Set(['front', 'model']), new Set(['high', 'medium']));
console.log(JSON.stringify({results, high, highMedium, highLow, intersect,
  unchanged: original === JSON.stringify(frame)}));
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

    assert [n["id"] for n in data["high"]["nodes"]] == ["entry"]
    assert {n["id"] for n in data["highMedium"]["nodes"]} == {
        "entry",
        "sibling",
        "alternate",
        "leaf",
    }
    assert {n["id"] for n in data["highLow"]["nodes"]} == {"entry", "middle", "leaf"}
    assert {n["id"] for n in data["intersect"]["nodes"]} == {"entry", "sibling"}


def test_graph_node_rejects_unknown_importance():
    from harness_common.report_kit import GraphNode

    with pytest.raises(ValueError, match="importance"):
        GraphNode("api", "API", importance="urgent")


@pytest.mark.parametrize(
    "entry_value, selected, expected",
    [
        (2, ["= 0"], {"entry", "zero"}),
        (2, ["N/A"], {"entry", "missing"}),
        (2, ["< 入口"], {"entry", "small"}),
        (2, ["≥ 入口"], {"entry", "equal", "large"}),
        (2, ["< 入口", "≥ 入口"], {"entry", "small", "equal", "large", "leaf"}),
        (
            2,
            ["= 0", "N/A", "< 入口", "≥ 入口"],
            {"entry", "zero", "missing", "small", "equal", "large", "leaf"},
        ),
        (0, ["< 入口"], {"entry"}),
        (0, ["≥ 入口"], {"entry", "small", "equal", "large", "leaf"}),
        (None, [], {"entry", "small", "equal", "large", "leaf"}),
    ],
)
def test_qps_filter_uses_original_entry_and_prunes_unreachable_descendants(
    entry_value, selected, expected
):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed to execute the browser graph filter")
    assets = files("harness_common.report_kit").joinpath("assets")
    frame = {
        "entry_ids": ["entry"],
        "nodes": [
            dict(id=name, group="service", importance="high", comparison_value=value)
            for name, value in [
                ("entry", entry_value),
                ("zero", 0),
                ("missing", None),
                ("small", 1),
                ("equal", 2),
                ("large", 5),
                ("leaf", 8),
            ]
        ],
        "edges": [
            dict(source="entry", target=name)
            for name in ["zero", "missing", "small", "equal", "large"]
        ]
        + [dict(source="small", target="leaf")],
    }
    script = """
const fs = require('fs'), vm = require('vm');
vm.runInThisContext(fs.readFileSync(process.argv[1] + '/graph-filter.js', 'utf8'));
const {frame, selected} = JSON.parse(fs.readFileSync(0, 'utf8'));
const original = JSON.stringify(frame);
const result = rkFilterGraph(frame, new Set(['service']), new Set(['high']), new Set(selected));
const multiple = {...frame, entry_ids: ['entry', 'zero']};
console.log(JSON.stringify({ids: result.nodes.map(n=>n.id), reference: rkEntryValue(frame),
  multipleReference: rkEntryValue(multiple), unchanged: original === JSON.stringify(frame)}));
"""
    result = subprocess.run(
        [node, "-e", script, str(assets)],
        input=json.dumps(dict(frame=frame, selected=selected)),
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    actual = json.loads(result.stdout)
    assert set(actual["ids"]) == expected
    assert actual["reference"] == entry_value
    assert actual["multipleReference"] is None
    assert actual["unchanged"]
