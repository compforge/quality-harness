import assert from "node:assert/strict";
import { createServer } from "node:http";
import { connect } from "node:net";
import { createServer as createHttpsServer } from "node:https";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { KubernetesClient } from "../../dist/kubernetes/client.js";
import { KubectlExecutor } from "../../dist/kubernetes/executor.js";

const tls = process.argv[2] === "tls";
const proxied = process.argv[2] === "proxy";
const cert = name => readFileSync(fileURLToPath(new URL(`./pod-log-tls/${name}`, import.meta.url)));
let connections = 0;
let requests = 0;
const directory = mkdtempSync(join(tmpdir(), "toolbox-node-kubernetes-reuse-"));
const handler = (request, response) => {
  requests++;
  assert.equal(request.headers.authorization, "Bearer fixture-token");
  if (tls) assert.equal(request.socket.authorized, true);
  response.end(JSON.stringify({ items: [] }));
};
const server = tls ? createHttpsServer({ key: cert("server-key.pem"), cert: cert("server-cert.pem"),
  ca: cert("ca.pem"), requestCert: true, rejectUnauthorized: true }, handler) : createServer(handler);
const sockets = new Set();
server.on("connection", socket => {
  connections++;
  sockets.add(socket);
  socket.once("close", () => sockets.delete(socket));
});
await new Promise(resolve => server.listen(0, resolve));
const port = server.address().port;
let proxyConnections = 0;
const proxy = createServer();
if (proxied) {
  proxy.on("connect", (request, socket, head) => {
    proxyConnections++;
    assert.equal(request.headers["proxy-authorization"], `Basic ${Buffer.from("fixture-user:fixture-pass").toString("base64")}`);
    assert.equal(request.url, `localhost:${port}`);
    const upstream = connect(port, "localhost", () => {
      socket.write("HTTP/1.1 200 Connection Established\r\n\r\n");
      if (head.length) upstream.write(head);
      socket.pipe(upstream); upstream.pipe(socket);
    });
    socket.on("error", () => upstream.destroy());
    upstream.on("error", () => socket.destroy());
  });
  await new Promise(resolve => proxy.listen(0, resolve));
}
const kubeconfig = join(directory, "config.json");
writeFileSync(kubeconfig, JSON.stringify({ apiVersion: "v1", kind: "Config", "current-context": "fixture",
  clusters: [{ name: "fixture", cluster: { server: `${tls ? "https" : "http"}://localhost:${port}`,
    ...(tls ? { "certificate-authority-data": cert("ca.pem").toString("base64") } : {}),
    ...(proxied ? { "proxy-url": `http://fixture-user:fixture-pass@localhost:${proxy.address().port}` } : {}),
  } }],
  contexts: [{ name: "fixture", context: { cluster: "fixture", user: "fixture" } }],
  users: [{ name: "fixture", user: { token: "fixture-token", ...(tls ? {
    "client-certificate-data": cert("client-cert.pem").toString("base64"),
    "client-key-data": cert("client-key.pem").toString("base64"),
  } : {}) } }],
}));
const kube = { namespace: "first", kubeconfig };
const executor = new KubectlExecutor(kube);
executor.run = () => { throw new Error("Unexpected kubectl fallback"); };
const client = new KubernetesClient(kube, undefined, executor);
try {
  await client.initialize();
  for (const namespace of ["first", "second", "third"]) {
    const result = await client.run(namespace, ["get", "pods", "-o", "json"]);
    assert.equal(result.ok, true, result.stderr);
    assert.equal(result.command[0], "kubernetes-api");
  }
  assert.equal(requests, 3);
  assert.equal(connections, 1, "sequential requests must reuse the physical connection");
  await client.dispose();
  // Observe peer closure before server.close(), which would itself close idle connections in Node.
  await new Promise((resolve, reject) => {
    if (!sockets.size) return resolve();
    const timer = setTimeout(() => reject(new Error("Pooled connection survived disposal")), 1000);
    for (const socket of sockets) socket.once("close", () => {
      if (!sockets.size) { clearTimeout(timer); resolve(); }
    });
  });
  assert.equal(proxyConnections, proxied ? 1 : 0);
  await new Promise(resolve => server.close(resolve));
  if (proxied) await new Promise(resolve => proxy.close(resolve));
  console.log(JSON.stringify({ requests, connections, disposed: true, tls }));
} finally {
  await client.dispose();
  server.closeAllConnections();
  server.close();
  proxy.closeAllConnections();
  proxy.close();
  rmSync(directory, { recursive: true, force: true });
}
