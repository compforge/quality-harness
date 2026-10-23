import assert from "node:assert/strict";
import { createServer } from "node:net";
import { MysqlClient } from "../../dist/mysql/index.js";
import { DirectTransport } from "../../dist/transport/index.js";

let accepted;
const connected = new Promise(resolve => { accepted = resolve; });
const server = createServer(socket => {
  socket.resume();
  accepted();
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const controller = new AbortController();
const reason = new Error("host cancelled handshake");
const client = new MysqlClient({
  resolve: async () => ({ host: "127.0.0.1", port: server.address().port, database: "db", user: "reader", password: "" }),
  transports: [new DirectTransport()],
}, { signal: controller.signal, connectTimeoutMs: 30_000, queryTimeoutMs: 30_000 });
const initialized = client.initialize().catch(error => error);
await connected;
controller.abort(reason);
assert.equal(await initialized, reason);
await client.dispose();
await new Promise(resolve => server.close(resolve));
console.log("disposed");
