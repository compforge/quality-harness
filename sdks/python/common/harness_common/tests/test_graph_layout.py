"""Exercise the real bundled offline layout, including dense groups and viewports."""

import json
import shutil
import subprocess
from importlib.resources import files

import pytest


@pytest.mark.parametrize(
    "viewport", [{"width": 1600, "height": 900}, {"width": 600, "height": 900}]
)
def test_grouped_layout_preserves_operations_and_avoids_overlap(viewport):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed to execute the browser layout bundle")
    assets = files("harness_common.report_kit").joinpath("assets")
    # A dense fan-out with varying sizes, a connected group, a cycle, and an ungrouped node.
    nodes = [{"id": "entry", "group": "entry", "width": 110, "height": 110}]
    nodes += [
        {"id": f"op{i}", "group": "dense", "width": 75 + i * 3, "height": 75 + i * 3}
        for i in range(24)
    ]
    nodes += [
        {"id": "step1", "group": "pipeline", "width": 130, "height": 100},
        {"id": "step2", "group": "pipeline", "width": 130, "height": 100},
        {"id": "loose", "width": 80, "height": 80},
    ]
    edges = [{"source": "entry", "target": f"op{i}"} for i in range(24)]
    edges += [
        {"source": "op0", "target": "step1"},
        {"source": "step1", "target": "step2"},
        {"source": "step2", "target": "entry"},
        {"source": "step2", "target": "loose"},
    ]
    script = """
const fs = require('fs'), vm = require('vm');
const [assets] = process.argv.slice(1);
const ELK = require(assets + '/elk.bundled.js');
vm.runInThisContext(fs.readFileSync(assets + '/graph-layout.js', 'utf8'));
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
(async () => {
  const layout = await rkLayout(input.nodes, input.edges, input.viewport, new ELK());
  const empty = await rkLayout([], [], input.viewport, new ELK());
  console.log(JSON.stringify({...layout, positions: [...layout.positions], empty: empty.positions.size}));
})().catch(e => { console.error(e); process.exit(1); });
"""
    result = subprocess.run(
        [node, "-e", script, str(assets)],
        input=json.dumps(dict(nodes=nodes, edges=edges, viewport=viewport)),
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
    layout = json.loads(result.stdout)
    positions = dict(layout["positions"])
    assert set(positions) == {n["id"] for n in nodes}
    assert layout["empty"] == 0
    assert layout["width"] > 0 and layout["height"] > 0
    # All actual operation rectangles remain distinct, including inside a packed group.
    for i, left in enumerate(nodes):
        for right in nodes[i + 1 :]:
            a, b = positions[left["id"]], positions[right["id"]]
            assert (
                abs(a["x"] - b["x"]) >= (left["width"] + right["width"]) / 2
                or abs(a["y"] - b["y"]) >= (left["height"] + right["height"]) / 2
            )
    dense = [positions[f"op{i}"] for i in range(24)]
    assert len({p["x"] for p in dense}) > 1
    assert len({p["y"] for p in dense}) > 1
    if viewport["width"] > viewport["height"]:
        assert 0.8 < layout["width"] / layout["height"] < 3.5
