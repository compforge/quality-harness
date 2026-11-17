import type { ExecResult } from "../../executor";
import type { KubernetesPod } from "../index";

export interface ServicePodListResult {
  serviceCapture: ExecResult;
  podCapture: ExecResult;
  byService: Record<string, string[]>;
  pods: KubernetesPod[];
  parseError?: string;
}

export interface PodLogRequest {
  pod: string;
  container?: string;
  allContainers?: boolean;
  prefix?: boolean;
  previous?: boolean;
  tail?: number;
  since?: string;
  sinceTime?: string;
  /** Inclusive runtime timestamp boundary, enforced while streaming by the API transport. */
  untilTime?: string;
  /** 单次 Container 日志响应的服务端字节上限。 */
  limitBytes?: number;
  /** 指定后 stdout 原样流式写入该文件，返回值不再在内存中保留 stdout。 */
  rawFilePath?: string;
  onLine?: (line: string) => void;
  /** A streaming sink may own persistence without retaining another copy in stdout. */
  collectStdout?: boolean;
}

export type PodLogCaptureStatus = "complete" | "partial" | "unavailable";

export interface PodLogResult extends ExecResult {
  /** complete=完整读完；partial=保留了部分证据；unavailable=没有取得可用日志。 */
  captureStatus: PodLogCaptureStatus;
  reason?: string;
  bytesRead: number;
  attempts: number;
  /** Replayed from another capture in this root execution; bytesRead only charges the owner. */
  reused?: boolean;
}

/** Pod 枚举与日志读取能力；调用方决定采哪个 Pod，infra 决定如何通过 Kubernetes 采。 */
export interface KubernetesPodLogAccess {
  clientVersion(): Promise<ExecResult>;
  listServicePods(services: readonly string[]): Promise<ServicePodListResult>;
  collectPodLogs(request: PodLogRequest): Promise<PodLogResult>;
}

