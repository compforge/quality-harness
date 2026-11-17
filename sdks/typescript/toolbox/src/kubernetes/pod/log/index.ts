/** @spec Pod log consumers use resource-owned entries; transport and capture implementation paths stay private. */
export type { ServicePodListResult, PodLogRequest, PodLogCaptureStatus, PodLogResult, KubernetesPodLogAccess } from "./types";
export { KubectlPodLogAccess } from "./kubectl";
export { ClientNodePodLogAccess, DEFAULT_CLIENT_NODE_LOG_POLICY } from "./client-node";
export type { ClientNodeFetch, ClientNodeLogPolicy, ClientNodePodLogOptions } from "./client-node";
export { logTimestampNanos } from "./timestamp";
