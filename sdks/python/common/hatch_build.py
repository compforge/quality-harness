"""Embed repository-owned browser assets in every harness-common distribution."""

from pathlib import Path
from shutil import copytree, rmtree


def prepare_assets(root: Path) -> Path:
    target = root / "harness_common/report_kit/assets"
    source = (root / "../../../report-kit").resolve()
    if (source / "graph/view.js").is_file():
        if target.exists():
            rmtree(target)
        for directory in ("graph", "vendor", "tabs"):
            copytree(source / directory, target / directory)
    elif not (target / "graph/view.js").is_file():
        raise RuntimeError("report-kit sources or bundled sdist assets are required")
    return target


if __name__ == "__main__":
    prepare_assets(Path(__file__).resolve().parent)
else:
    from hatchling.builders.hooks.plugin.interface import BuildHookInterface

    class CustomBuildHook(BuildHookInterface):
        def initialize(self, version, build_data):
            # @spec Wheels and sdists include the same resources, even outside the repository.
            assets = prepare_assets(Path(self.root))
            build_data["force_include"][str(assets)] = (
                "harness_common/report_kit/assets"
            )
