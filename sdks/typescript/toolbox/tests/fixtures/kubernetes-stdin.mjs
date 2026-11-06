import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { KubernetesClient } from '../../dist/kubernetes/client.js';

// @case stdin-eof-compat: stdin never starts a native session against an incompatible server.
const root = mkdtempSync(join(tmpdir(), 'toolbox-stdin-'));
const callsFile = join(root, 'calls');
let nativeRequests = 0;
const server = createServer((_req, res) => { nativeRequests++; res.writeHead(403); res.end(); });
server.on('upgrade', (_req, socket) => { nativeRequests++; socket.end('HTTP/1.1 403 Forbidden\r\n\r\n'); });
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const config = join(root, 'config');
writeFileSync(config, JSON.stringify({ apiVersion: 'v1', kind: 'Config', 'current-context': 'fixture',
  contexts: [{ name: 'fixture', context: { cluster: 'fixture', user: 'fixture' } }],
  clusters: [{ name: 'fixture', cluster: { server: `http://127.0.0.1:${server.address().port}` } }],
  users: [{ name: 'fixture', user: {} }] }));
writeFileSync(callsFile, '');
writeFileSync(join(root, 'kubectl'), `#!/usr/bin/env node
const { appendFileSync } = require('node:fs');
appendFileSync(${JSON.stringify(callsFile)}, JSON.stringify(process.argv.slice(2)) + '\\n');
const mode = process.argv.at(-1);
if (mode === 'wait') {
  process.on('SIGTERM', () => {});
  process.stdout.write('ready'); setInterval(() => {}, 10000);
} else if (mode === 'overflow') {
  process.stdout.write('x'.repeat(2048)); setInterval(() => {}, 10000);
} else if (mode === 'stderr-overflow') {
  process.stderr.write('x'.repeat(2048)); setInterval(() => {}, 10000);
} else {
  const chunks = [];
  process.stdin.on('data', chunk => chunks.push(chunk));
  process.stdin.on('end', () => {
    process.stdout.write(Buffer.concat(chunks));
    process.stderr.write('fixture stderr');
    process.exitCode = mode === 'exit' ? 7 : 0;
  });
}
`, { mode: 0o700 });
process.env.PATH = `${root}:${process.env.PATH}`;
const client = new KubernetesClient({ kubeconfig: config, context: 'fixture', namespace: 'demo' }, undefined, undefined,
  { timeoutMs: 3000, concurrency: 1, maxBytes: 1024 });
const target = { pod: 'pod', container: 'app' };
const exec = (mode, opts = {}) => client.exec('other', target, [mode], { stdin: '', ...opts });
const calls = () => readFileSync(callsFile, 'utf8').trim().split('\n').filter(Boolean).map(line => JSON.parse(line));
try {
  const bytes = Buffer.from('你好\0');
  for (const stdin of ['', 'text', bytes, new ReadableStream({ start(c) { c.enqueue(bytes); c.close(); } })]) {
    const result = await exec('echo', { stdin });
    assert.equal(result.ok, true); assert.equal(result.exitCode, 0);
    assert.equal(result.stdout, typeof stdin === 'string' ? stdin : bytes.toString());
    assert.equal(result.stderr, 'fixture stderr'); assert.equal(result.command[0], 'kubectl');
    assert.deepEqual(result.command.slice(1), ['--kubeconfig', config, '--context', 'fixture', '-n', 'other',
      'exec', '-i', 'pod', '-c', 'app', '--', 'echo']);
  }
  let streamed = ''; const raw = [];
  const result = await exec('exit', { stdin: bytes, collectStdout: false,
    onStdout: text => streamed += text, onStdoutBytes: chunk => raw.push(chunk) });
  assert.equal(result.ok, false); assert.equal(result.exitCode, 7); assert.equal(result.stdout, '');
  assert.equal(streamed, bytes.toString()); assert.deepEqual(Buffer.concat(raw), bytes);
  for (const mode of ['overflow', 'stderr-overflow']) {
    const before = calls().length;
    await assert.rejects(exec(mode), { kind: 'limit_exceeded' });
    assert.equal(calls().length, before + 1);
  }
  const callbackError = new Error('consumer failed');
  await assert.rejects(exec('wait', { onStdout: () => { throw callbackError; } }), error => error === callbackError);
  let entered;
  const ready = new Promise(resolve => entered = resolve);
  const controller = new AbortController();
  const reason = new Error('caller cancelled');
  const active = exec('wait', { signal: controller.signal, onStdout: () => entered() });
  const rejected = assert.rejects(active, error => error === reason);
  await ready;
  assert.equal((await client.exec('demo', target, ['queued-native'], { timeoutMs: 40 })).timedOut, true);
  const before = calls().length;
  assert.equal((await exec('queued-stdin', { timeoutMs: 40 })).timedOut, true);
  assert.equal(calls().length, before);
  controller.abort(reason); await rejected;
  const timeout = await exec('wait', { timeoutMs: 150 });
  assert.equal(timeout.timedOut, true); assert.equal(timeout.command[0], 'kubectl');
  assert.equal((await exec('echo')).ok, true);
  let disposalEntered;
  const disposalReady = new Promise(resolve => disposalEntered = resolve);
  let inputCancelled = false;
  const pending = exec('wait', { stdin: new ReadableStream({ cancel() { inputCancelled = true; } }),
    onStdout: () => disposalEntered() });
  const queued = exec('queued');
  const settled = Promise.allSettled([pending, queued]);
  await disposalReady; await client.dispose();
  assert.deepEqual((await settled).map(r => r.status), ['rejected', 'rejected']);
  assert.equal(inputCancelled, true);
  assert.equal(nativeRequests, 0);
  assert.ok(!calls().some(argv => argv.at(-1) === 'queued'));
  // CLI stdin retains kubeconfig proxy settings even when native Bun proxy sessions are unsupported.
  const proxyConfig = JSON.parse(readFileSync(config, 'utf8'));
  proxyConfig.clusters[0].cluster['proxy-url'] = 'http://127.0.0.1:1';
  writeFileSync(config, JSON.stringify(proxyConfig));
  const cliClient = new KubernetesClient({ kubeconfig: config, namespace: 'demo' });
  try {
    const result = await cliClient.exec('demo', target, ['echo'], { stdin: 'proxied' });
    assert.equal(result.stdout, 'proxied');
    process.env.PATH = root + '/missing';
    const missing = await cliClient.exec('demo', target, ['echo'], { stdin: 'data' });
    assert.equal(missing.ok, false); assert.equal(missing.exitCode, null);
    assert.match(missing.stderr, /ENOENT|not found/);
  } finally { await cliClient.dispose(); }
  console.log('disposed');
} finally {
  await client.dispose();
  await new Promise(resolve => server.close(resolve));
  rmSync(root, { recursive: true, force: true });
}
