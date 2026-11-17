import { expect, test } from "bun:test";
import { spawnSync } from "node:child_process";

// @spec Socket evidence comes from the actual Node request, with no replay or secret-bearing URL capture.
test("Node S3 failures retain request milestones, isolate concurrent calls and release observers", () => {
  const result = spawnSync("node", ["--input-type=module", "-e", `
    import assert from 'node:assert/strict';
    import { createServer } from 'node:http';
    import dns from 'node:dns';
    import { channel } from 'node:diagnostics_channel';
    import { S3Client, S3Error, probeS3ReadWrite } from ${JSON.stringify(new URL("../dist/s3/index.js", import.meta.url).href)};
    import { HttpClient } from ${JSON.stringify(new URL("../dist/http/index.js", import.meta.url).href)};
    import { DirectTransport } from ${JSON.stringify(new URL("../dist/transport/index.js", import.meta.url).href)};
    const servers = [], clients = [], transports = [], calls = [];
    const serve = async handler => {
      const server = createServer(handler); servers.push(server);
      await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
      return 'http://127.0.0.1:' + server.address().port;
    };
    const connect = async (endpoint, timeout = 400, forcePathStyle = true) => {
      const transport = new DirectTransport(); transports.push(transport);
      const client = new S3Client({ endpoint, forcePathStyle, region: 'us-east-1', credentials: { accessKeyId: 'private-ak', secretAccessKey: 'private-sk' } },
        transport, { concurrency: 1, connectTimeoutMs: 150, requestTimeoutMs: timeout });
      clients.push(client); await client.initialize(); return client;
    };
    const put = client => client.putObject('bucket', 'private-object-name', Buffer.from('private-body'), {maxBytes: 30});
    try {
      const resetUrl = await serve((req, res) => { calls.push('reset'); req.resume(); req.on('end', () => req.socket.destroy()); });
      const slowUrl = await serve((req, res) => { calls.push('slow'); req.resume(); });
      const reset = await connect(resetUrl), slow = await connect(slowUrl);
      const failures = await Promise.all([put(reset).catch(e => e), put(slow).catch(e => e)]);
      const lost = failures[0].toJSON(), timeout = failures[1].toJSON();
      assert.equal(lost.code, 'ECONNRESET'); assert.equal(lost.kind, 'connection_lost');
      assert.equal(lost.request.nativeRequestObserved, true); assert.equal(lost.request.phase, 'request_sent');
      assert.equal(lost.request.socket.remoteAddress, '127.0.0.1');
      assert.ok(lost.request.timings.connected >= 0); assert.ok(lost.request.timings.sent >= 0);
      assert.ok(lost.request.causeCodes.includes('ECONNRESET'));
      assert.equal(timeout.code, 'ETIMEDOUT'); assert.equal(timeout.request.timeoutSource, 'operation');
      assert.equal(timeout.request.phase, 'request_sent'); assert.ok(timeout.request.elapsedMs >= 350);
      assert.notEqual(lost.request.request.port, timeout.request.request.port);
      assert.deepEqual(calls.sort(), ['reset', 'slow']);
      for (const secret of ['private-ak', 'private-sk', 'private-object-name', 'private-body', 'Authorization', 'X-Amz']) {
        assert.ok(!JSON.stringify([lost, timeout]).includes(secret));
      }
      const original = lost.request.request.port; lost.request.request.port = 1;
      assert.equal(failures[0].toJSON().request.request.port, original);
      const lookup = dns.lookup;
      let stalled;
      try {
        dns.lookup = function(hostname, ...args) {
          if (hostname !== 'stall.invalid') return lookup.call(this, hostname, ...args);
          // A pending DNS lookup makes connectionTimeout deterministic without external network access.
          const callback = args.at(-1);
          stalled = setTimeout(() => callback(null, [{address:'127.0.0.1',family:4}]), 2000); stalled.unref();
        };
        const client = await connect('http://stall.invalid:12345', 800);
        const failure = await put(client).catch(e => e.toJSON());
        assert.equal(failure.request.timeoutSource, 'http_handler');
        assert.equal(failure.request.phase, 'socket_assigned');
        assert.equal(failure.request.timings.connected, undefined);
        assert.ok(failure.request.elapsedMs < 700);
      } finally { dns.lookup = lookup; clearTimeout(stalled); }

      // Virtual-host addressing changes the actual dial host even on an unmapped route.
      const virtualUrl = await serve((req, res) => { req.resume(); req.on('end', () => req.socket.destroy()); });
      try {
        dns.lookup = function(hostname, ...args) {
          if (hostname !== 'bucket.s3.example.test') return lookup.call(this, hostname, ...args);
          const callback = args.at(-1);
          setImmediate(() => args[0]?.all
            ? callback(null, [{address:'127.0.0.1',family:4}])
            : callback(null, '127.0.0.1', 4));
        };
        const client = await connect(virtualUrl.replace('127.0.0.1', 's3.example.test'), 800, false);
        const failure = await put(client).catch(e => e.toJSON());
        assert.equal(failure.request.request.host, 'bucket.s3.example.test');
        assert.equal(failure.request.request.dialHost, 'bucket.s3.example.test');
        assert.equal(failure.request.socket.resolvedAddress, '127.0.0.1');
      } finally { dns.lookup = lookup; }

      let reusedCalls = 0;
      const reuseUrl = await serve((req, res) => {
        req.resume(); req.on('end', () => {
          if (++reusedCalls === 1) res.writeHead(200, {'ETag':'test'}).end();
          else req.socket.destroy();
        });
      });
      const reused = await connect(reuseUrl); await put(reused);
      const reuseFailure = await put(reused).catch(e => e.toJSON());
      assert.equal(reuseFailure.request.socket.reused, true);
      assert.equal(reuseFailure.request.timings.lookup, undefined);
      assert.equal(reuseFailure.request.phase, 'request_sent');

      const serverTimeoutUrl = await serve((req, res) => {
        req.resume(); res.writeHead(408, {'Content-Type':'application/xml','x-amz-request-id':'request-1'});
        res.end('<Error><Code>RequestTimeout</Code><Message>private-server-detail</Message></Error>');
      });
      const serverTimeout = await connect(serverTimeoutUrl);
      const serverFailure = await put(serverTimeout).catch(e => e.toJSON());
      assert.equal(serverFailure.httpStatus, 408); assert.equal(serverFailure.requestId, 'request-1');
      assert.equal(serverFailure.request.phase, 'response_headers');
      assert.equal(serverFailure.request.timeoutSource, undefined);
      assert.ok(!JSON.stringify(serverFailure).includes('private-server-detail'));
      const cancelled = new AbortController(), reason = new Error('caller cancellation');
      const pending = put(slow); // A separate operation still has independent diagnostics.
      const aborted = slow.headBucket('bucket', {signal: cancelled.signal}).catch(e => e);
      cancelled.abort(reason); assert.equal(await aborted, reason); await pending.catch(() => {});

      const downloadUrl = await serve((req, res) => {
        res.writeHead(200, {'Content-Length': '100'}); res.write('partial');
        setTimeout(() => res.destroy(), 20);
      });
      const transport = new DirectTransport(); transports.push(transport);
      const http = new HttpClient({endpoint: downloadUrl}, transport, {concurrency: 1, connectTimeoutMs: 150, requestTimeoutMs: 400, maxResponseBytes: 1000});
      clients.push(http);
      const error = await http.request('/private-object?X-Amz-Signature=private-signed-url').catch(e => e);
      const evidence = S3Error.from(error).toJSON();
      assert.equal(evidence.request.phase, 'response_headers');
      assert.ok(evidence.request.timings.headers >= 0); assert.ok(!JSON.stringify(evidence).includes('private-'));

      const probe = await probeS3ReadWrite({bucket:'bucket', client:reset, signal:new AbortController().signal,
        cleanupTimeoutMs:200, presignExpiresInSeconds:60, download:async()=>{throw new Error('must not download');}});
      assert.equal(probe.steps[0].failure.request.phase, 'request_sent');
      assert.equal(probe.steps[1].status, 'not-run'); assert.equal(probe.steps[3].status, 'not-run');
      assert.equal(channel('http.client.request.created').hasSubscribers, false);
      assert.equal(channel('http.client.request.start').hasSubscribers, false);
    } finally {
      await Promise.all(clients.map(client => client.dispose()));
      await Promise.all(transports.map(transport => transport.dispose()));
      for (const server of servers) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
    }
  `], { encoding: "utf8", timeout: 15_000 });
  expect(result.stderr).toBe("");
  expect(result.status).toBe(0);
}, 20_000);
