package testrun

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/compforge/quality-harness/sdks/go/e2e/caserun"
	"github.com/compforge/quality-harness/sdks/go/report"
)

func passingResult() caserun.Result {
	return caserun.Run(
		context.Background(), caserun.Ref("demo", "happy"), nil, &struct{}{},
		caserun.Definition[struct{}]{
			Execute: func(context.Context, *struct{}) error { return nil },
			Budgets: caserun.Budgets{Execute: time.Second},
		},
	)
}

func TestFinishWritesCanonicalRunVerdict(t *testing.T) {
	runsDir := t.TempDir()
	s := New("demo-service", WithRunsDir(runsDir), WithRunID("run-1"), WithOutput(&bytes.Buffer{}))
	s.Assert(t, passingResult())

	verdict, path, err := s.Finish()
	if err != nil {
		t.Fatal(err)
	}
	wantPath := filepath.Join(runsDir, "demo-service", "run-1", "verdict.json")
	if path != wantPath || verdict.Status != report.StatusPass || len(verdict.Cases) != 1 {
		t.Fatalf("finish = path %q verdict %+v, want path %q with one passing case", path, verdict, wantPath)
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("stat verdict: %v", err)
	}
}

func TestFinishWritesNothingWhenNoCasesRan(t *testing.T) {
	runsDir := t.TempDir()
	verdict, path, err := New("demo-service", WithRunsDir(runsDir), WithRunID("run-1")).Finish()
	if err != nil || path != "" || verdict.Status != report.StatusSkipped {
		t.Fatalf("empty finish = path %q verdict %+v error %v", path, verdict, err)
	}
	entries, err := os.ReadDir(runsDir)
	if err != nil || len(entries) != 0 {
		t.Fatalf("empty run created artifacts: entries=%v error=%v", entries, err)
	}
}

func TestMainMakesVerdictWriteFailureFailTheRun(t *testing.T) {
	root := t.TempDir()
	notDirectory := filepath.Join(root, "file")
	if err := os.WriteFile(notDirectory, []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	var output bytes.Buffer
	s := New("demo-service", WithRunsDir(notDirectory), WithRunID("run-1"), WithOutput(&output))
	s.Assert(t, passingResult())
	if code := s.Main(func() int { return 0 }); code != 1 {
		t.Fatalf("exit code = %d, want 1", code)
	}
	if output.Len() == 0 {
		t.Fatal("verdict write failure was not reported")
	}
}

func TestMainRejectsInvalidRunIdentityBeforeRunningTests(t *testing.T) {
	var output bytes.Buffer
	runCalled := false
	s := New("invalid scope", WithRunID("run-1"), WithOutput(&output))
	code := s.Main(func() int {
		runCalled = true
		return 0
	})
	if code != 1 || runCalled {
		t.Fatalf("Main = code %d runCalled %v, want code 1 without running tests", code, runCalled)
	}
	if output.Len() == 0 {
		t.Fatal("invalid run configuration was not reported")
	}
}
