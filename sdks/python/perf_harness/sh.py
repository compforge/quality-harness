"""Tiny async subprocess helper, shared by the k8s-touching modules."""

from __future__ import annotations

import asyncio


async def run_capture(cmd: list[str], timeout: float = 300.0) -> str:
    """Run ``cmd``, return stdout; raise RuntimeError with stderr on failure."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as e:
        proc.kill()
        raise RuntimeError(f"timeout after {timeout}s: {' '.join(cmd)}") from e
    if proc.returncode != 0:
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(cmd)}\n{err.decode()}")
    return out.decode()
