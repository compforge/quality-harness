"""Run a project's native test command with an execution-owned connection path."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, suppress
import logging
import os
from pathlib import Path
import signal

from harness_toolbox.socks import SocksProxy
from harness_toolbox.transport import Transport

LOG = logging.getLogger(__name__)


async def _stop_group(process: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        await process.wait()
        return
    try:
        await asyncio.wait_for(process.wait(), 2)
    except TimeoutError:
        pass
    finally:
        # The parent may have exited while a descendant ignored TERM. Retire
        # survivors without relying on signal-0 probes of a disappearing group.
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    await process.wait()


async def run_command(
    command: Sequence[str],
    connection: AbstractAsyncContextManager[Transport],
    *,
    cwd: str | Path,
    env: Mapping[str, str] | None = None,
    prepare: Callable[[Transport], Awaitable[Mapping[str, str]]] | None = None,
    timeout_s: float = 900,
) -> int:
    """Preserve the native exit code/output; do not synthesize an E2E Verdict.

    ``prepare`` owns project readiness checks and optional child env overrides.
    Proxy overrides apply only to the child. POSIX cancellation/timeout stops its
    process group before closing connections; fixture cleanup remains project-owned.
    This helper neither retries commands nor installs/deploys their dependencies.
    """
    if os.name != "posix":
        raise ValueError("command transport currently requires a POSIX runner")
    async with connection as transport, SocksProxy(transport) as proxy:
        child_env = {**os.environ, **(env or {})}
        async with asyncio.timeout(timeout_s):
            if prepare:
                child_env.update(await prepare(transport))
            child_env.update(proxy.environment())
            process = await asyncio.create_subprocess_exec(
                *command, cwd=cwd, env=child_env, start_new_session=True
            )
            try:
                result = await process.wait()
                LOG.info("test command exited: code=%s", result)
                return result
            finally:
                # A make/CLI parent may exit before its children. Always retire the
                # owned group; unrelated processes and remote resources are untouched.
                await _stop_group(process)
