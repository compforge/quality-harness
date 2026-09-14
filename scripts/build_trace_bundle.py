#!/usr/bin/env python3
"""Build the trace skill wheel and the independently packaged common/toolbox wheels.

Usage: python3 scripts/build_trace_bundle.py --out /tmp/trace-wheels
Third-party runtime wheels are locked separately by the consuming skill.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    sdk = repo / "sdks" / "python"
    version = tomllib.loads((sdk / "pyproject.toml").read_text())["project"]["version"]
    common_version = tomllib.loads((sdk / "common/pyproject.toml").read_text())["project"]["version"]
    args.out.mkdir(parents=True, exist_ok=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    source_hash = hashlib.sha256()
    for directory in (sdk / "common", sdk / "toolbox", sdk / "trace_harness"):
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.suffix in (".py", ".toml") and "tests" not in path.parts and "__pycache__" not in path.parts:
                source_hash.update(str(path.relative_to(sdk)).encode())
                source_hash.update(path.read_bytes())
    wheels = []

    def build(root: Path, package: str, package_version: str):
        subprocess.run(["uv", "build", "--wheel", "--out-dir", str(args.out.resolve()), str(root)],
                       check=True, env={**os.environ, "SOURCE_DATE_EPOCH": "315532800"})
        path = args.out / f"{package}-{package_version}-py3-none-any.whl"
        wheels.append({"distribution": package.replace("_", "-"), "filename": path.name,
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "imports": [package]})

    for name in ("common", "toolbox"):
        package_version = tomllib.loads((sdk / name / "pyproject.toml").read_text())["project"]["version"]
        build(sdk / name, f"harness_{name}", package_version)
    with tempfile.TemporaryDirectory(prefix="trace-wheel-") as temporary:
        root = Path(temporary)
        shutil.copytree(sdk / "trace_harness", root / "trace_harness",
                        ignore=shutil.ignore_patterns("tests", "__pycache__", "*.pyc"))
        dependencies = [f"harness-common=={common_version}", "httpx>=0.27,<1", "pyyaml>=6,<7"]
        (root / "pyproject.toml").write_text(
            '[build-system]\nrequires = ["hatchling==1.27.0"]\nbuild-backend = "hatchling.build"\n'
            f'[project]\nname = "trace-harness"\nversion = "{version}"\n'
            'requires-python = ">=3.11"\nlicense = {text = "Apache-2.0"}\n'
            f'dependencies = {json.dumps(dependencies)}\n'
            '[tool.hatch.build.targets.wheel]\npackages = ["trace_harness"]\n')
        build(root, "trace_harness", version)
    (args.out / "dependencies.lock").write_text(json.dumps({
        "schemaVersion": 1, "pythonRequires": ">=3.11",
        "source": {"repository": "quality-harness", "commit": commit, "version": version,
                   "contentSha256": source_hash.hexdigest(), "workingTree": True},
        "builder": {"backend": "hatchling", "sourceDateEpoch": 315532800},
        "wheels": wheels}, indent=2) + "\n")


if __name__ == "__main__":
    main()
