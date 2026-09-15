"""Bounded subprocess execution shared by perf's Kubernetes operations."""

from harness_toolbox.process import execute


async def run_capture(cmd: list[str], timeout: float = 300.0) -> str:
    """Run argv with bounded output and cancellation-safe process cleanup."""
    try:
        result = await execute(cmd, timeout_s=timeout)
    except TimeoutError as exc:
        raise RuntimeError(f"timeout after {timeout}s: {' '.join(cmd)}") from exc
    if result.exit_code:
        raise RuntimeError(
            f"command failed ({result.exit_code}): {' '.join(cmd)}\n{result.stderr.decode()}"
        )
    return result.stdout.decode()
