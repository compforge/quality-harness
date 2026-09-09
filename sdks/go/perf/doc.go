// Package perf is the Go home for the load/capacity test SDK (perf), the
// counterpart of python/perf_harness. It is a deliberate placeholder: the Go side
// tracks the Python shape and backfills perf only once that shape stabilizes.
//
// The e2e / eval / perf split exists in the Go tree now — not because perf is
// implemented, but so import paths and the package boundary settle before code
// arrives. The three SDKs do not import each other (先复制后收敛); the only
// shared package is report.
package perf
