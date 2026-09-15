import asyncio
from contextlib import asynccontextmanager
import os
import sys

import pytest

from e2e_harness.command import run_command
from harness_toolbox.transport import DirectTransport


@asynccontextmanager
async def connection(events):
    events.append("open")
    try:
        yield DirectTransport()
    finally:
        events.append("close")


async def test_native_exit_code_and_child_environment(tmp_path, monkeypatch):
    events = []
    monkeypatch.setenv("HTTP_PROXY", "ambient")

    async def prepare(transport):
        events.append("prepare")
        return {"PROJECT_FIXTURE": "ready"}

    code = "import os,sys; assert os.environ['HTTP_PROXY'].startswith('socks5://127.0.0.1:'); assert os.environ['NO_PROXY']==''; assert os.environ['PROJECT_FIXTURE']=='ready'; sys.exit(7)"
    result = await run_command(
        [sys.executable, "-c", code], connection(events), cwd=tmp_path, prepare=prepare
    )
    assert result == 7
    assert events == ["open", "prepare", "close"]
    assert os.environ["HTTP_PROXY"] == "ambient"


async def test_prepare_failure_never_starts_command(tmp_path):
    events = []

    async def prepare(transport):
        raise ConnectionError("not ready")

    with pytest.raises(ConnectionError, match="not ready"):
        await run_command(
            ["command-must-not-start"],
            connection(events),
            cwd=tmp_path,
            prepare=prepare,
        )
    assert events == ["open", "close"]


async def test_timeout_cleans_process_group_and_connection(tmp_path):
    events = []
    pid_file = tmp_path / "pid"
    code = "import os,pathlib,time; pathlib.Path('pid').write_text(str(os.getpid())); time.sleep(60)"
    with pytest.raises(TimeoutError):
        await run_command(
            [sys.executable, "-c", code],
            connection(events),
            cwd=tmp_path,
            timeout_s=0.5,
        )
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert events == ["open", "close"]


async def test_cancel_closes_connection(tmp_path):
    events = []
    task = asyncio.create_task(
        run_command(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            connection(events),
            cwd=tmp_path,
        )
    )
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events == ["open", "close"]
