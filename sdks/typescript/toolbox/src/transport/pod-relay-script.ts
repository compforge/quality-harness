/** Private wire protocol: control travels over exec stdin; database bytes only over TCP. */
export const POD_RELAY_SCRIPT = String.raw`
import asyncio, json, sys

async def main():
    config = json.loads(sys.argv[1])
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=4096)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer)
    servers = []
    active = set()

    async def pipe(source, destination):
        while True:
            chunk = await source.read(65536)
            if not chunk:
                if destination.can_write_eof():
                    destination.write_eof()
                return
            destination.write(chunk)
            await destination.drain()

    async def relay(source, destination, host, port):
        task = asyncio.current_task()
        if len(active) >= config["maxConnections"]:
            destination.close()
            await destination.wait_closed()
            return
        active.add(task)
        upstream = None
        pumps = []
        try:
            remote, upstream = await asyncio.wait_for(
                asyncio.open_connection(host, port), config["connectTimeoutMs"] / 1000)
            pumps = [asyncio.create_task(pipe(source, upstream)), asyncio.create_task(pipe(remote, destination))]
            await asyncio.gather(*pumps)
        except (OSError, asyncio.TimeoutError):
            pass
        finally:
            for pump in pumps:
                pump.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            for writer in (upstream, destination):
                if writer:
                    writer.close()
            for writer in (upstream, destination):
                if writer:
                    try:
                        await writer.wait_closed()
                    except OSError:
                        pass
            active.discard(task)

    print(json.dumps({"ready": True}), flush=True)
    try:
        while True:
            # EOF cleans up normal shutdown; lease expiry bounds an orphan after lost exec transport.
            line = await asyncio.wait_for(reader.readline(), config["leaseMs"] / 1000)
            if not line:
                break
            request = json.loads(line)
            if request.get("heartbeat"):
                continue
            identifier = request["id"]
            try:
                if len(servers) >= config["maxTargets"]:
                    raise ValueError("target limit")
                host, port = request["host"], request["port"]
                server = await asyncio.start_server(
                    lambda r, w, h=host, p=port: relay(r, w, h, p), "127.0.0.1", 0)
                servers.append(server)
                print(json.dumps({"id": identifier, "port": server.sockets[0].getsockname()[1]}), flush=True)
            except (OSError, ValueError):
                print(json.dumps({"id": identifier, "error": "relay listener unavailable"}), flush=True)
    except asyncio.TimeoutError:
        pass
    finally:
        for server in servers:
            server.close()
        await asyncio.gather(*(server.wait_closed() for server in servers))
        tasks = list(active)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

asyncio.run(main())
`;
