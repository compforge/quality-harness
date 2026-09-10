"""Bounded subprocess I/O with cancellation and timeout cleanup."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass


async def stop(process: asyncio.subprocess.Process) -> None:
    async def drain(stream: asyncio.StreamReader | None) -> None:
        if stream is not None:
            while await stream.read(65536):
                pass

    if process.stdin is not None:
        process.stdin.close()
    # A full pipe can delay wait() even after exit. Drain after readers have stopped.
    drains = [
        asyncio.create_task(drain(process.stdout)),
        asyncio.create_task(drain(process.stderr)),
    ]
    try:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 2)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    process.kill()
        await process.wait()
        await asyncio.gather(*drains)
    finally:
        for task in drains:
            task.cancel()
        await asyncio.gather(*drains, return_exceptions=True)


@asynccontextmanager
async def process_scope(command: Sequence[str]) -> AsyncIterator[asyncio.subprocess.Process]:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        yield process
    finally:
        await stop(process)


async def read_bounded(stream: asyncio.StreamReader, limit: int) -> bytes:
    chunks = bytearray()
    while chunk := await stream.read(min(65536, limit + 1 - len(chunks))):
        chunks.extend(chunk)
        if len(chunks) > limit:
            raise ValueError("subprocess output exceeds byte limit")
    return bytes(chunks)


@dataclass(frozen=True)
class ExecResult:
    stdout: bytes
    stderr: bytes
    exit_code: int


async def execute(
    command: Sequence[str],
    *,
    stdin: bytes = b"",
    timeout_s: float = 60,
    max_bytes: int = 64 * 1024 * 1024,
) -> ExecResult:
    async with asyncio.timeout(timeout_s), process_scope(command) as process:
        assert process.stdin and process.stdout and process.stderr

        async def write() -> None:
            assert process.stdin
            try:
                process.stdin.write(stdin)
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()

        tasks = [
            asyncio.create_task(read_bounded(process.stdout, max_bytes)),
            asyncio.create_task(read_bounded(process.stderr, 65536)),
            asyncio.create_task(write()),
        ]
        try:
            stdout, stderr, _ = await asyncio.gather(*tasks)
            return ExecResult(stdout, stderr, await process.wait())
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def run(
    command: Sequence[str],
    *,
    stdin: bytes = b"",
    timeout_s: float = 60,
    max_bytes: int = 64 * 1024 * 1024,
) -> bytes:
    result = await execute(command, stdin=stdin, timeout_s=timeout_s, max_bytes=max_bytes)
    if result.exit_code:
        raise RuntimeError(f"subprocess failed (exit status {result.exit_code})")
    return result.stdout
