import assert from "node:assert/strict";
import net from "node:net";
import mysql from "mysql2";
import { MysqlClient } from "../../dist/mysql/index.js";
import { DirectTransport } from "../../dist/transport/index.js";

// Neither the listener nor the peer keeps Node alive. Only a leaked client socket can do that.
const server = net.createServer({ allowHalfOpen: true }, socket => {
  socket.unref();
  socket.on("error", () => {});
  const connection = mysql.createConnection({ isServer: true, stream: socket });
  connection.on("error", () => {});
  connection.serverHandshake({ protocolVersion: 10, serverVersion: "8.0.0", connectionId: 1,
    statusFlags: 2, characterSet: 45, capabilityFlags: 0x00088201,
    authCallback: (_data, done) => {
      done(null);
      // The fixture starts a new packet sequence for each client command after authentication.
      connection.sequenceId = 0;
    } });
  const rejectQuery = () => connection.writeError({ code: 1146, message: "fixture SQL rejected" });
  connection.on("query", rejectQuery);
  connection.on("stmt_prepare", rejectQuery);
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
server.unref();
const target = { host: "127.0.0.1", port: server.address().port, user: "fixture", password: "", database: "" };
const client = new MysqlClient({ resolve: async () => target, transports: [new DirectTransport()] },
  { connectTimeoutMs: 1000, queryTimeoutMs: 1000 });
await client.initialize();
if (process.argv[2] === "query-error") {
  await assert.rejects(client.database.query(target, "SELECT 1", []), /fixture SQL rejected/);
} else if (process.argv[2] === "readonly-error") {
  await assert.rejects(client.database.queryReadonly(target, "SELECT 1", [],
    { timeoutMs: 1000, maxRows: 1, maxBytes: 100 }), /fixture SQL rejected/);
}
await client.dispose();
await client.dispose();
console.log("disposed");
// No process.exit or socket unref on the client: the parent asserts natural process exit.
