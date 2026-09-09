package report

import "testing"

func TestBuildRunVerdictErrorWins(t *testing.T) {
	verdict := BuildRunVerdict("sandbox", "r1", []CaseVerdict{
		{CaseID: "ok", Status: StatusPass},
		{CaseID: "bad", Status: StatusFail},
		{CaseID: "broken", Status: StatusError, Reason: "cleanup failed"},
	})
	if verdict.Status != StatusError || verdict.Harness != "e2e" {
		t.Fatalf("verdict = %+v", verdict)
	}
	if verdict.Reason != "1 fail, 1 error; first error — broken: cleanup failed" {
		t.Fatalf("reason = %q", verdict.Reason)
	}
}
