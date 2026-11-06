import type { Endpoint } from "@compforge/harness-common";
import { ProtocolError } from "../errors.js";
import { validateEndpoint } from "./endpoint.js";
export interface ConnectivityStage {
  stage: "dns" | "tcp" | "tls";
  status: "passed" | "failed";
  elapsedMs: number;
  addresses?: string[];
  address?: string;
  error?: string;
}
export interface ConnectivityResult {
  target: Endpoint;
  stages: ConnectivityStage[];
  status: "passed" | "failed";
}
export interface ConnectivityExecutor {
  (command: readonly string[], options: { stdin: string; timeoutMs: number; signal?: AbortSignal }): Promise<string>;
}

// Only protocol facts leave the target. Exception messages may contain configuration and are not returned.
const SCRIPT = `import json,socket,ssl,sys,time
q=json.load(sys.stdin)
start=time.monotonic()
deadline=start+q["timeoutMs"]/1000
stages=[]
def remaining():
    t=deadline-time.monotonic()
    if t<=0: raise TimeoutError()
    return t
def add(stage,t,**values):
    stages.append(dict(stage=stage,elapsedMs=round((time.monotonic()-t)*1000,3),**values))
phase="dns"
t=time.monotonic()
sock=None
try:
    addresses=socket.getaddrinfo(q["host"],q["port"],type=socket.SOCK_STREAM)
    if not addresses: raise socket.gaierror()
    add(phase,t,status="passed",addresses=list(dict.fromkeys(a[4][0] for a in addresses))[:8])
    phase="tcp"
    t=time.monotonic()
    failure=None
    for family,kind,proto,_,address in addresses[:8]:
        candidate=socket.socket(family,kind,proto)
        try:
            candidate.settimeout(remaining())
            candidate.connect(address)
            sock=candidate
            break
        except OSError as e:
            failure=e
            candidate.close()
    if sock is None: raise failure or OSError()
    add(phase,t,status="passed",address=sock.getpeername()[0])
    if q.get("tls"):
        phase="tls"
        t=time.monotonic()
        sock.settimeout(remaining())
        sock=ssl.create_default_context().wrap_socket(sock,server_hostname=q["host"])
        add(phase,t,status="passed")
except (OSError,ValueError) as e:
    add(phase,t,status="failed",error=type(e).__name__)
finally:
    if sock is not None: sock.close()
print(json.dumps(dict(stages=stages,status="failed" if any(s["status"]=="failed" for s in stages) else "passed")))`;

/**
 * @spec DNS, TCP and optional verified TLS are observed from the supplied executor's network context.
 * @rule A hostname (including service.namespace) and an IP use the same path; connectivity requires an explicit port.
 * @spec secure uses verified direct TLS for https/wss/tls; other encrypted protocols throw ProtocolError(code=Unsupported) before I/O.
 * @rule Tool failure/timeout throws; it is not evidence that the target is unhealthy. No ICMP, credentials or HTTP policy is implied.
 */
export async function probeConnectivity(target: Endpoint, execute: ConnectivityExecutor,
  options: { timeoutMs: number; signal?: AbortSignal }): Promise<ConnectivityResult> {
  validateEndpoint(target);
  const endpoint: Endpoint = { host: target.host, port: target.port,
    ...(target.scheme !== undefined ? { scheme: target.scheme } : {}),
    ...(target.secure !== undefined ? { secure: target.secure } : {}) };
  if (endpoint.secure && !["https", "wss", "tls"].includes(endpoint.scheme ?? "")) {
    throw new ProtocolError(`Encrypted connectivity is not supported for scheme ${endpoint.scheme ?? "(unspecified)"}; supported schemes: https, wss, tls`, { code: "Unsupported" });
  }
  if (!Number.isFinite(options.timeoutMs) || options.timeoutMs <= 0) throw new Error("Connectivity timeout must be positive");
  options.signal?.throwIfAborted();
  const raw = await execute(["python3", "-c", SCRIPT], { ...options, stdin: JSON.stringify({ host: endpoint.host, port: endpoint.port, tls: endpoint.secure, timeoutMs: options.timeoutMs }) });
  options.signal?.throwIfAborted();
  const value = JSON.parse(raw) as Omit<ConnectivityResult, "target">;
  const expected = ["dns", "tcp", ...(endpoint.secure ? ["tls"] : [])];
  if (!value || !Array.isArray(value.stages) || !value.stages.length || value.stages.length > expected.length
    || !["passed", "failed"].includes(value.status)
    || value.stages.some((stage, index) => !stage || stage.stage !== expected[index] || !["passed", "failed"].includes(stage.status)
      || !Number.isFinite(stage.elapsedMs) || stage.elapsedMs < 0
      || (index < value.stages.length - 1 && stage.status !== "passed"))
    || (value.status === "passed" ? value.stages.length !== expected.length || value.stages.some(stage => stage.status !== "passed")
      : value.stages.at(-1)?.status !== "failed")) throw new Error("Invalid connectivity probe result");
  return { target: endpoint, stages: value.stages, status: value.status };
}
