"""Common remains usable without protocol clients or third-party packages."""

import subprocess
import sys
from pathlib import Path


def test_common_imports_without_site_packages():
    # @spec Common's complete runtime import surface needs only the standard library.
    package_root = Path(__file__).resolve().parents[2]
    subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            """
import importlib
import pkgutil
import sys

sys.path.insert(0, sys.argv[1])
import harness_common

for module in pkgutil.walk_packages(harness_common.__path__, "harness_common."):
    if ".tests" not in module.name:
        importlib.import_module(module.name)

from harness_common import ClientManager, Execution, Service
from harness_common.access.client import ClientManager as AccessClientManager
from harness_common.execution import Execution as PackageExecution
from harness_common.execution.execution import Execution as FactExecution
from harness_common.system.service import Service as SystemService

assert ClientManager is AccessClientManager
assert Execution is PackageExecution is FactExecution
assert Service is SystemService
assert not any(name.startswith("harness_toolbox") for name in sys.modules)
""",
            str(package_root),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
