"""The sdist-to-wheel path must retain offline assets without the checkout at runtime."""

import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile


def test_distribution_embeds_report_kit_and_renders_outside_checkout(tmp_path):
    package = Path(__file__).resolve().parents[2]
    source = package.parents[2] / "report-kit"
    subprocess.run(
        ["uv", "build", "--project", str(package), "--out-dir", str(tmp_path)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    wheel = next(tmp_path.glob("*.whl"))
    with ZipFile(wheel) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names))
        for folder in ("graph", "vendor"):
            for path in (source / folder).iterdir():
                relative = path.relative_to(source).as_posix()
                assert (
                    archive.read(f"harness_common/report_kit/assets/{relative}")
                    == path.read_bytes()
                )
    # @case An isolated interpreter loads only the wheel, including zip-backed resources.
    subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            """
import sys
sys.path.insert(0, sys.argv[1])
from harness_common.report_kit import Chart, GraphFrame, GraphView, Report, Section, render_html
html = render_html(Report('packaged', sections=[Section('views', [
    GraphView('offline', [GraphFrame('window', [], [])]), Chart('trend', [])])]))
assert 'function rkGraph' in html and 'echarts.init' in html
assert '.graph-view .graph-canvas' in html
assert '<script src=' not in html
""",
            str(wheel),
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
