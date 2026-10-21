import assert from 'node:assert/strict';
import { KubernetesClient } from '../../dist/kubernetes/client.js';
const client = new KubernetesClient({ kubeconfig: process.argv[2], namespace: 'demo' });
try {
  if (process.argv[3] === 'unsupported') {
    await assert.rejects(client.exec('demo', { pod: 'pod' }, ['once']), { kind: 'unsupported_operation' });
  } else {
  await assert.rejects(client.exec('demo', { pod: 'pod' }, ['forbidden']),
    process.versions.bun ? { kind: 'connection_failed' } : { kind: 'permission_denied', code: 403 });
  const result = await client.exec('demo', { pod: 'pod' }, ['echo']);
  assert.equal(result.stdout, 'hi\0'); assert.equal(result.ok, true);
  assert.equal((await client.exec('demo', { pod: 'pod' }, ['wait'], { timeoutMs: 30 })).timedOut, true);
  const active = client.exec('demo', { pod: 'pod' }, ['wait']);
  const settled = Promise.allSettled([active]);
  await new Promise(resolve => setTimeout(resolve, 30));
  await client.dispose();
  assert.equal((await settled)[0].status, 'rejected');
  }
} finally { await client.dispose(); }
