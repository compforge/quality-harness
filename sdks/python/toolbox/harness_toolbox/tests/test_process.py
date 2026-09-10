import asyncio
import sys

import pytest

from harness_toolbox.process import run


async def test_process_output_is_bounded_and_timeout_drains_child():
    with pytest.raises(ValueError, match="byte limit"):
        await run([sys.executable, "-c", "print('x' * 1000000)"], max_bytes=10)
    with pytest.raises(TimeoutError):
        await run([sys.executable, "-c", "import time; time.sleep(60)"], timeout_s=0.05)
    assert (
        await run([sys.executable, "-c", "import sys; print(sys.stdin.read())"], stdin=b"payload")
        == b"payload\n"
    )


async def test_process_cancellation():
    task = asyncio.create_task(run([sys.executable, "-c", "import time; time.sleep(60)"]))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)
