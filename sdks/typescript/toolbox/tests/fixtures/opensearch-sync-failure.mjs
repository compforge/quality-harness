import assert from 'node:assert/strict';
import https from 'node:https';
import { OpenSearchEngine } from '../../dist/opensearch/index.js';

// Inject the runtime's synchronous TLS failure without depending on platform-specific IP SNI behavior.
const original = https.request;
const failure = Object.assign(new TypeError('invalid TLS servername'), { code: 'ERR_INVALID_ARG_VALUE' });
https.request = () => { throw failure; };
const client = new OpenSearchEngine({ node: 'https://127.0.0.1:9200', auth: {} });
try { await assert.rejects(client.ping(), error => error === failure); }
finally { https.request = original; await client.close(); }
console.log('disposed');
// No forced exit: the parent deadline detects leaked SDK close timers.
