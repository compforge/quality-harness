package caserun

import (
	"testing"

	"github.com/compforge/quality-harness/sdks/go/report"
)

// Assert adapts a pure CaseRun result to go test without putting testing.T in
// the runner, lifecycle, or judge contracts.
func Assert(t *testing.T, result Result) {
	t.Helper()
	switch result.Status {
	case report.StatusPass:
		return
	case report.StatusSkipped:
		t.Skip(result.Reason)
	default:
		t.Errorf("case %s[%s] %s: %s", result.Ref.ID, result.Variant.ID(), result.Status, result.Reason)
	}
}
