import {expect, test} from "bun:test";
import type {Workload, ScrapeTarget} from "@compforge/harness-common";
import {matchesScrapeTarget} from "../src/metricdb";

test("matches Kubernetes services, pod selectors and resource identities within their workload namespace", () => {
  const target: ScrapeTarget = {state:"active", labels:{namespace:"other", service:"other"}, discoveredLabels:{
    __meta_kubernetes_namespace:"observed", __meta_kubernetes_service_name:"api",
    __meta_kubernetes_pod_label_app_kubernetes_io_name:"worker", __meta_kubernetes_pod_name:"worker-0",
    __meta_kubernetes_pod_controller_kind:"StatefulSet", __meta_kubernetes_pod_controller_name:"worker",
  }};
  const match = (location: Workload["location"], namespace?: string) =>
    matchesScrapeTarget({name:"service", workloads:[{name:"workload", platform:"kubernetes", namespace, location}]}, "observed", target);
  expect(matchesScrapeTarget({name:"api",workloads:[]}, "observed", target)).toBe(true);
  expect(matchesScrapeTarget({name:"other",workloads:[]}, "other", target)).toBe(false);
  expect(match({kind:"service",name:"api"})).toBe(true);
  expect(match({kind:"service",name:"api"}, "other")).toBe(false);
  expect(match({kind:"labels",labels:{"app.kubernetes.io/name":"worker"}})).toBe(true);
  expect(match({kind:"labels",labels:{}})).toBe(false);
  expect(match({kind:"resource",resource_kind:"Pod",name:"worker-0"})).toBe(true);
  expect(match({kind:"resource",resource_kind:"StatefulSet",name:"worker"})).toBe(true);
  expect(match({kind:"resource",resource_kind:"Deployment",name:"worker"})).toBe(false);
});
