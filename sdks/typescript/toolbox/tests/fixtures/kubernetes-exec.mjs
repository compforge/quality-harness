import assert from 'node:assert/strict';
import { createServer } from 'node:https';
import { createServer as createProxy } from 'node:http';
import { connect } from 'node:net';
import { WebSocketServer } from 'ws';
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const cert = name => readFileSync(new URL(`./pod-log-tls/${name}`, import.meta.url));
const root = mkdtempSync(join(tmpdir(), 'toolbox-native-exec-node-'));
const server = createServer({ key: cert('server-key.pem'), cert: cert('server-cert.pem'),
  ca: cert('ca.pem'), requestCert: true, rejectUnauthorized: true });
const ws = new WebSocketServer({ server, verifyClient: ({ req }, done) => {
  done(!req.url.includes('command=forbidden'), 403);
} });
let calls = 0;
ws.on('connection', (socket, request) => {
  calls++;
  assert.equal(request.socket.authorized, true);
  assert.equal(request.headers.authorization, 'Bearer fixture');
  const command = new URL(request.url, 'http://fixture').searchParams.get('command');
  if (command === 'wait') return;
  socket.send(Buffer.from([1, 104, 105, 0]));
  socket.send(Buffer.concat([Buffer.from([3]), Buffer.from('{"status":"Success"}')]));
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const overridden = process.argv[3] === 'proxy-name';
const proxied = process.argv[3] !== 'direct';
const unsupported = process.argv[2] === 'bun' && proxied;
const proxy = createProxy();
let proxyCalls = 0;
if (proxied) {
  proxy.on('connect', (request, socket, head) => {
    proxyCalls++;
    const upstream = connect(server.address().port, '127.0.0.1', () => {
      socket.write('HTTP/1.1 200 Connection Established\r\n\r\n');
      if (head.length) upstream.write(head);
      socket.pipe(upstream); upstream.pipe(socket);
    });
    socket.on('close', () => upstream.destroy());
    upstream.on('close', () => socket.destroy());
    socket.on('error', () => upstream.destroy());
    upstream.on('error', () => socket.destroy());
  });
  await new Promise(resolve => proxy.listen(0, '127.0.0.1', resolve));
}
const kubeconfig = join(root, 'config');
writeFileSync(kubeconfig, JSON.stringify({ apiVersion: 'v1', kind: 'Config', 'current-context': 'fixture',
  contexts: [{ name: 'fixture', context: { cluster: 'fixture', user: 'fixture' } }],
  clusters: [{ name: 'fixture', cluster: { server: `https://${overridden ? '127.0.0.1' : 'localhost'}:${server.address().port}`,
    ...(proxied ? { 'proxy-url': `http://127.0.0.1:${proxy.address().port}` } : {}),
    'certificate-authority-data': cert('ca.pem').toString('base64'), 'tls-server-name': 'localhost' } }],
  users: [{ name: 'fixture', user: { token: 'fixture',
    'client-certificate-data': cert('client-cert.pem').toString('base64'),
    'client-key-data': cert('client-key.pem').toString('base64') } }] }));
try {
  const child = spawn(process.argv[2] ?? "node", [fileURLToPath(new URL('./kubernetes-exec-client.mjs', import.meta.url)), kubeconfig, unsupported ? 'unsupported' : 'supported'],
    { stdio: ['ignore', 'pipe', 'pipe'] });
  let stderr = '';
  child.stderr.on('data', chunk => stderr += chunk);
  const code = await new Promise((resolve, reject) => { child.on('error', reject); child.on('exit', resolve); });
  assert.equal(code, 0, stderr);
  assert.equal(calls, unsupported ? 0 : 3);
  if (proxied) assert.equal(proxyCalls, unsupported ? 0 : 4);
} finally {
  for (const socket of ws.clients) socket.terminate();
  await new Promise(resolve => ws.close(resolve));
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
  if (proxied) await new Promise(resolve => proxy.close(resolve));
  rmSync(root, { recursive: true, force: true });
}
console.log('disposed');
