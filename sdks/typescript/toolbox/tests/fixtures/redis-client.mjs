import assert from 'node:assert/strict';
import { createServer } from 'node:net';
const { RedisClient } = await import(process.env.REDIS_PACKAGE_ENTRY);
const mode = process.argv[2];
const sockets = new Set();
let writes = 0;
let started;
const active = new Promise(resolve => { started = resolve; });

function reply(value) {
  if (Array.isArray(value)) return `*${value.length}\r\n${value.map(reply).join('')}`;
  if (typeof value === 'number') return `:${value}\r\n`;
  return `$${Buffer.byteLength(value)}\r\n${value}\r\n`;
}
function commands(buffer) {
  const output = [];
  let cursor = 0;
  while (cursor < buffer.length) {
    const start = cursor;
    const end = buffer.indexOf('\r\n', cursor);
    if (end < 0) break;
    const count = Number(buffer.subarray(cursor + 1, end).toString());
    cursor = end + 2;
    const args = [];
    for (let index = 0; index < count; index++) {
      const end = buffer.indexOf('\r\n', cursor);
      if (end < 0) return { output, rest: buffer.subarray(start) };
      const length = Number(buffer.subarray(cursor + 1, end).toString());
      cursor = end + 2;
      if (buffer.length < cursor + length + 2) return { output, rest: buffer.subarray(start) };
      args.push(buffer.subarray(cursor, cursor + length).toString());
      cursor += length + 2;
    }
    output.push(args);
  }
  return { output, rest: buffer.subarray(cursor) };
}
// Read the handshake before sending FIN so this specifically tests an unexpected graceful close.
const refused = createServer(socket => {
  socket.on('error', () => {});
  socket.once('data', () => socket.end());
});
const server = createServer(socket => {
  sockets.add(socket);
  socket.on('close', () => sockets.delete(socket));
  socket.on('error', () => {});
  let buffer = Buffer.alloc(0);
  socket.on('data', chunk => {
    buffer = Buffer.concat([buffer, chunk]);
    const parsed = commands(buffer);
    buffer = parsed.rest;
    for (const args of parsed.output) {
      if (args[0] === 'INCR') { writes++; socket.destroy(); break; }
      if (args[0] === 'GET' && mode === 'cancel') { started(); continue; }
      const value = args[0] === 'PING' ? 'PONG'
        : args[0] === 'INFO' ? `cluster_enabled:${mode === 'cluster' ? 1 : 0}\r\n`
        : args[0] === 'CLUSTER' ? [[0, 16383, ['redis-master', 6379]]]
        : args[0] === 'SENTINEL' ? (args[1] === 'GET-MASTER-ADDR-BY-NAME' ? ['redis-master', '6379'] : [])
        : 'OK';
      socket.write(reply(value));
    }
  });
});
await new Promise(resolve => refused.listen(0, '127.0.0.1', resolve));
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const routes = [];
const target = { clusterType: mode === 'sentinel' ? 'sentinel' : mode === 'cluster' ? 'cluster' : 'single',
  endpoints: [{ host: 'seed.internal', port: 6379 }], database: 0, useSsl: false,
  username: 'data-user', password: 'data-password', timeoutMs: 1000,
  ...(mode === 'sentinel' ? { sentinelMasterName: 'primary', sentinelUsername: 'sentinel-user', sentinelPassword: 'sentinel-password' } : {}) };
const client = new RedisClient({ resolve: async () => target, transports: [
  { kind: 'tcp', name: 'native', connect: async () => ({ host: '127.0.0.1', port: refused.address().port }) },
  { kind: 'tcp', name: 'pod-relay', connect: async () => ({ host: '127.0.0.1', port: server.address().port }) },
] }, { onRoute: route => routes.push(route) });
try {
  await client.initialize();
  if (mode === 'sentinel' || mode === 'cluster') {
    const topology = await client.topology();
    assert.equal(topology.clusterType, mode);
    assert.equal(topology.masters[0].host, 'redis-master');
    const node = await client.access.connection(topology.masters[0], 0);
    assert.equal(await node.ping(), 'PONG');
    assert.equal(routes.length, 2);
  } else {
    const node = await client.access.connection(target.endpoints[0], 0);
    if (mode === 'no-replay') {
      await assert.rejects(node.command(['INCR', 'counter']));
      assert.equal(writes, 1);
      assert.equal(routes.length, 1);
    } else if (mode === 'cancel') {
      const pending = node.command(['GET', 'key']).catch(error => error);
      await active;
      await client.dispose();
      assert.match((await pending).message, /disposed/);
    } else assert.equal(await node.ping(), 'PONG');
  }
  assert.ok(routes.every(route => route.transport === 'pod-relay' && route.reason === 'connection_lost'), JSON.stringify(routes));
} finally {
  await client.dispose();
  await new Promise(resolve => refused.close(resolve));
  await new Promise(resolve => server.close(resolve));
}
console.log('disposed');
