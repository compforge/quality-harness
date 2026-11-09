# Go trajectory measurements

Deterministic measurements over agent-ledger `observation.SessionView` facts.
This independent Go 1.26 module reuses native call identities and containment;
it neither replays calls nor introduces another Trajectory/Step schema. Python
continues to consume ATIF. Shared fixtures keep overlapping measurement semantics
and the Session/Global JSON projection aligned.

```go
runner := trajectory.DefaultRunner()
run, err := runner.Run(ctx, sessions)
// Handle err before projecting.
views, err := trajectory.Project(run, trajectory.Metadata{
    RunID: "analysis-1", DatasetID: "review-1", DatasetVersion: recordingDigest,
    CreatedAt: time.Now().UTC(),
})
```

Default measurers cover model call/token/duration usage and tool call/failure/
duration usage. Missing fields remain absent; recorded zero remains zero. Tool
failure ratios use all observed calls as the denominator and expose known-status
coverage separately. Input totals include cache reads and writes; cache reads
are already included in agent-ledger's input field, while cache writes are added.
Cumulative call duration can overlap and is not wall time.

A registered `Measurer` declares its catalog, aggregation and semantic version.
`Run` produces results; `Project` only aggregates them. Adding a measurement needs
no renderer change. Exact duplicate Sessions are measured once; conflicting
snapshots fail. Linked Sessions are not expanded. Plugin errors become result
health, never zero measurements. Global excludes per-Session evidence.

Consumers own scheduling and persistence. `Runner.Fingerprint()` includes the
projection contract and catalog versions; increment a Measurer's version when
its calculation changes. Combine that fingerprint with recording content hashes
for cache identity. Cache writes must verify their inputs remained unchanged.

This initial Go module does not implement Python's Detector, Verifier, context,
retry, or report-generation features. Go SDK tests and the Python trajectory
conformance test consume `conformance/trajectory/session-measurements.json`.
