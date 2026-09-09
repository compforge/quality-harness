// Package kube provides namespace-scoped Kubernetes control and observation
// primitives for quality-harness SDKs and their consumers.
//
// It deliberately does not define cases, fault scenarios, load profiles, or
// verdicts. Callers decide which workload to target, when to disrupt it, and
// what behavior proves recovery; this package only performs and observes the
// Kubernetes operations reliably.
package kube
