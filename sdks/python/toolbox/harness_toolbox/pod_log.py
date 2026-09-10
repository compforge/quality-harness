"""Capture each physical Pod/container/window once, then filter locally for many IDs."""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from harness_toolbox.client import ClientProvider, data_source_key
from harness_toolbox.kube import KubernetesClient, KubernetesDataSource
from harness_toolbox.process import process_scope, read_bounded


@dataclass(frozen=True)
class PodLogTarget:
    pod: str
    uid: str
    container: str
    restart_count: int
    since: datetime
    until: datetime
    previous: bool = False

    def __post_init__(self) -> None:
        if not self.pod or not self.uid or not self.container:
            raise ValueError("Pod logs require physical Pod and container identity")
        if self.since.tzinfo is None or self.until.tzinfo is None or self.since >= self.until:
            raise ValueError("Pod logs require an absolute timezone-aware [since, until) window")

    @property
    def key(self) -> str:
        return data_source_key(
            "pod-log",
            {**asdict(self), "since": self.since.isoformat(), "until": self.until.isoformat()},
        )


@dataclass(frozen=True)
class PodLogCapture:
    path: Path
    size_bytes: int
    truncated: bool
    target: PodLogTarget

    def matching_lines(self, ids: tuple[str, ...]) -> dict[str, list[str]]:
        """One local scan; callers own business matching rules and output formatting."""
        result: dict[str, list[str]] = {identity: [] for identity in ids}
        with self.path.open(errors="replace") as stream:
            for line in stream:
                if self.truncated and not line.endswith("\n"):
                    continue
                stamp, separator, _ = line.partition(" ")
                if not separator:
                    continue
                try:
                    observed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if not self.target.since <= observed < self.target.until:
                    continue
                for identity in result:
                    if identity in line:
                        result[identity].append(line.rstrip("\n"))
        return result


@dataclass(frozen=True)
class PodLogDataSource:
    kubernetes: KubernetesDataSource
    concurrency: int = 4
    timeout_s: float = 60
    max_capture_bytes: int = 64 * 1024 * 1024
    max_total_bytes: int = 512 * 1024 * 1024

    @property
    def key(self) -> str:
        return data_source_key(
            "pod-logs",
            [
                self.kubernetes.key,
                self.concurrency,
                self.timeout_s,
                self.max_capture_bytes,
                self.max_total_bytes,
            ],
        )

    def create_client(self, clients: ClientProvider) -> PodLogClient:
        return PodLogClient(self, clients)


class PodLogClient:
    """The root shares this client/configuration to share its pool and byte budget."""

    def __init__(self, source: PodLogDataSource, clients: ClientProvider) -> None:
        if (
            min(
                source.concurrency,
                source.timeout_s,
                source.max_capture_bytes,
                source.max_total_bytes,
            )
            <= 0
        ):
            raise ValueError("Pod log limits must be positive")
        self._source = source
        self._clients = clients
        self._kubernetes: KubernetesClient | None = None
        self._slots = asyncio.Semaphore(source.concurrency)
        self._directory: tempfile.TemporaryDirectory[str] | None = None
        self._captures: dict[str, asyncio.Task[PodLogCapture]] = {}
        self._used_bytes = 0
        self._disposal: asyncio.Task[None] | None = None

    async def initialize(self) -> None:
        if self._disposal is not None:
            raise RuntimeError("Pod log client is disposed")
        if self._directory is None:
            self._kubernetes = await self._clients.get(self._source.kubernetes)
            self._directory = tempfile.TemporaryDirectory(prefix="pod-logs-")

    async def capture(self, target: PodLogTarget) -> PodLogCapture:
        if self._directory is None or self._disposal is not None:
            raise RuntimeError("Pod log client is not initialized")
        task = self._captures.get(target.key)
        if task is None:
            task = asyncio.create_task(self._capture(target))
            self._captures[target.key] = task
            task.add_done_callback(lambda done: self._settled(target.key, done))
        return await asyncio.shield(task)

    def _settled(self, key: str, task: asyncio.Task[PodLogCapture]) -> None:
        if (task.cancelled() or task.exception() is not None) and self._captures.get(key) is task:
            del self._captures[key]

    async def _check_identity(self, target: PodLogTarget) -> None:
        assert self._kubernetes is not None
        pod = await self._kubernetes.get_pod(target.pod)
        container = next((c for c in pod.containers if c.name == target.container), None)
        if (
            pod.uid != target.uid
            or container is None
            or container.restart_count != target.restart_count
        ):
            raise RuntimeError("Pod or container identity changed during log capture")

    async def _capture(self, target: PodLogTarget) -> PodLogCapture:
        assert self._directory is not None and self._kubernetes is not None
        path = Path(self._directory.name) / target.key.split(":")[1]
        written = 0
        truncated = False
        try:
            async with asyncio.timeout(self._source.timeout_s), self._slots:
                await self._check_identity(target)
                command = self._kubernetes.access.command(
                    "logs",
                    target.pod,
                    "-c",
                    target.container,
                    "--timestamps=true",
                    f"--since-time={target.since.isoformat()}",
                )
                if target.previous:
                    command.append("--previous")
                async with process_scope(command) as process:
                    assert process.stdout and process.stderr
                    stderr = asyncio.create_task(read_bounded(process.stderr, 65536))
                    try:
                        with path.open("wb") as stream:
                            while chunk := await process.stdout.read(65536):
                                available = min(
                                    self._source.max_capture_bytes - written,
                                    self._source.max_total_bytes - self._used_bytes,
                                )
                                accepted = chunk[:available]
                                # No await between accounting and write: all captures share one loop/budget.
                                self._used_bytes += len(accepted)
                                written += len(accepted)
                                stream.write(accepted)
                                if len(accepted) < len(chunk):
                                    truncated = True
                                    break
                        if not truncated:
                            if await process.wait():
                                raise RuntimeError("Pod log capture failed")
                            await stderr
                    finally:
                        stderr.cancel()
                        await asyncio.gather(stderr, return_exceptions=True)
                await self._check_identity(target)
            return PodLogCapture(path, written, truncated, target)
        except BaseException:
            self._used_bytes -= written
            path.unlink(missing_ok=True)
            raise

    async def dispose(self) -> None:
        if self._disposal is None:
            self._disposal = asyncio.create_task(self._dispose())
        await asyncio.shield(self._disposal)

    async def _dispose(self) -> None:
        tasks = list(self._captures.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._captures.clear()
        if self._directory is not None:
            self._directory.cleanup()
            self._directory = None
