"""Host observations shared by quality runners and project-owned fixtures."""

import platform
import shlex
from collections.abc import Sequence
from datetime import UTC, datetime

from harness_common.environment import EnvironmentFacts
from harness_common.host import Host


def command(host: Host | None, argv: Sequence[str]) -> list[str]:
    """Build local/SSH argv for process.execute's bounded I/O and timeout.

    Remote commands use the host's filesystem, including kubeconfig paths.
    Resource teardown remains the calling fixture's responsibility.
    """
    if not argv or not argv[0]:
        raise ValueError("host command requires a program")
    if host is not None:
        host.validate()
        if host.transport == "ssh":
            return [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                host.address,
                shlex.join(argv),
            ]
    return list(argv)


def observe_local() -> EnvironmentFacts:
    """Observe this process's host; never use these values for a remote target."""
    os_name = platform.system().lower()
    arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine(), platform.machine())
    values = {"os": os_name, "arch": arch, "kernel_version": platform.release()}
    if os_name == "linux":
        distribution = platform.freedesktop_os_release()
        values["distribution"] = distribution.get("ID", "")
        values["distribution_version"] = distribution.get("VERSION_ID", "")
    return EnvironmentFacts("local:uname,os-release", datetime.now(UTC).isoformat(), values)
