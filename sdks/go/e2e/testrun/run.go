// Package testrun adapts Go tests into one durable e2e Run.
package testrun

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sync/atomic"
	"testing"
	"time"

	"github.com/compforge/quality-harness/sdks/go/e2e/caserun"
	"github.com/compforge/quality-harness/sdks/go/report"
)

var pathSegmentPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]*$`)

type Option func(*Run)

func WithRunsDir(path string) Option {
	return func(r *Run) { r.runsDir = path }
}

func WithRunID(runID string) Option {
	return func(r *Run) { r.runID = runID }
}

func WithOutput(writer io.Writer) Option {
	return func(r *Run) { r.output = writer }
}

// Run owns one go test invocation's e2e identity and aggregate Verdict.
// Business cases still own their typed state, lifecycle steps, and assertions.
type Run struct {
	scope     string
	runsDir   string
	runID     string
	output    io.Writer
	recorder  caserun.Recorder
	count     atomic.Int64
	configErr error
}

func New(scope string, options ...Option) *Run {
	r := &Run{
		scope:   scope,
		runsDir: "runs",
		runID:   time.Now().UTC().Format("20060102T150405.000000000Z"),
		output:  os.Stderr,
	}
	for _, option := range options {
		option(r)
	}
	r.configErr = r.validateConfig()
	return r
}

func (r *Run) validateConfig() error {
	if !pathSegmentPattern.MatchString(r.scope) {
		return fmt.Errorf("e2e run scope %q is not a safe path segment", r.scope)
	}
	if !pathSegmentPattern.MatchString(r.runID) {
		return fmt.Errorf("e2e run id %q is not a safe path segment", r.runID)
	}
	return nil
}

// Assert records the CaseRun result before adapting it to testing.T. Recording
// first ensures fail, error, and skipped cases all remain visible in Verdict.
func (r *Run) Assert(t *testing.T, result caserun.Result) {
	t.Helper()
	r.recorder.Record(result)
	r.count.Add(1)
	caserun.Assert(t, result)
}

// Finish writes runs/<scope>/<run-id>/verdict.json. A test selection that did
// not execute any CaseRun writes nothing rather than creating a misleading
// empty run.
func (r *Run) Finish() (report.RunVerdict, string, error) {
	verdict := r.recorder.Verdict(r.scope, r.runID)
	if r.configErr != nil {
		return verdict, "", r.configErr
	}
	if r.count.Load() == 0 {
		return verdict, "", nil
	}
	path, err := report.WriteVerdict(filepath.Join(r.runsDir, r.scope, r.runID), verdict)
	return verdict, path, err
}

// Main wraps testing.M.Run for use from TestMain and makes Verdict persistence
// part of the run result. It returns an exit code so the caller retains the
// required os.Exit ownership:
//
//	func TestMain(m *testing.M) { os.Exit(systemRun.Main(m.Run)) }
func (r *Run) Main(run func() int) int {
	if r.configErr != nil {
		fmt.Fprintf(r.output, "configure e2e run: %v\n", r.configErr)
		return 1
	}
	code := run()
	_, path, err := r.Finish()
	if err != nil {
		fmt.Fprintf(r.output, "write e2e verdict: %v\n", err)
		if code == 0 {
			return 1
		}
		return code
	}
	if path != "" {
		fmt.Fprintf(r.output, "e2e verdict: %s\n", path)
	}
	return code
}
