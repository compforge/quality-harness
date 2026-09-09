package caserun

import (
	"context"
	"errors"
	"reflect"
	"testing"
	"time"

	"github.com/compforge/quality-harness/sdks/go/e2e/matrix"
	"github.com/compforge/quality-harness/sdks/go/report"
)

func testBudgets() Budgets {
	return Budgets{
		Prepare: time.Second,
		Execute: time.Second,
		Judge:   time.Second,
		Cleanup: time.Second,
	}
}

func TestRunFullLifecycleAndVerdictEvidence(t *testing.T) {
	state := []string{}
	appendStep := func(name string) Step[[]string] {
		return func(_ context.Context, state *[]string) error {
			*state = append(*state, name)
			return nil
		}
	}
	result := Run(
		context.Background(),
		Ref("sandbox-runtime", "idle_gc"),
		matrix.Variant{"executor": "supervisor"},
		&state,
		Definition[[]string]{
			Prepare: appendStep("prepare"),
			Execute: appendStep("execute"),
			Judge:   appendStep("judge"),
			Cleanup: appendStep("cleanup"),
			Budgets: testBudgets(),
			Facets:  map[string]string{"runtime": "bed"},
		},
	)
	if !reflect.DeepEqual(state, []string{"prepare", "execute", "judge", "cleanup"}) {
		t.Fatalf("phase order = %v", state)
	}
	if result.Status != report.StatusPass {
		t.Fatalf("status = %s, reason = %s", result.Status, result.Reason)
	}
	verdict := result.CaseVerdict()
	if verdict.ArmID != "executor=supervisor" || verdict.Facets["runtime"] != "bed" {
		t.Fatalf("verdict identity/facets = %+v", verdict)
	}
	if _, ok := verdict.Metrics["cleanup_duration_ms"]; !ok {
		t.Fatal("cleanup duration missing from verdict")
	}
}

func TestCleanupRunsAfterExecuteError(t *testing.T) {
	cleaned := false
	result := Run(
		context.Background(), Ref("sandbox-runtime", "execute_error"), nil, &cleaned,
		Definition[bool]{
			Execute: func(context.Context, *bool) error { return errors.New("request broke") },
			Cleanup: func(_ context.Context, state *bool) error {
				*state = true
				return nil
			},
			Budgets: testBudgets(),
		},
	)
	if !cleaned || result.Status != report.StatusError {
		t.Fatalf("cleaned=%v result=%+v", cleaned, result)
	}
}

func TestJudgeFailIsDistinctFromOperationalError(t *testing.T) {
	result := Run(
		context.Background(), Ref("sandbox-runtime", "eventual_gc"), nil, &struct{}{},
		Definition[struct{}]{
			Execute: func(context.Context, *struct{}) error { return nil },
			Judge:   func(context.Context, *struct{}) error { return Fail("carrier still exists") },
			Budgets: testBudgets(),
		},
	)
	if result.Status != report.StatusFail || result.Reason != "judge: carrier still exists" {
		t.Fatalf("result = %+v", result)
	}
}

func TestCleanupGetsIndependentBudgetAfterParentCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cleaned := false
	result := Run(
		ctx, Ref("sandbox-runtime", "cancelled"), nil, &cleaned,
		Definition[bool]{
			Execute: func(context.Context, *bool) error {
				cancel()
				return context.Canceled
			},
			Cleanup: func(ctx context.Context, state *bool) error {
				if err := ctx.Err(); err != nil {
					return err
				}
				*state = true
				return nil
			},
			Budgets: testBudgets(),
		},
	)
	if !cleaned || result.Phases[len(result.Phases)-1].Status != report.StatusPass {
		t.Fatalf("cleanup did not get an independent budget: %+v", result)
	}
}

func TestRunDetectsStepReturningAfterDeadline(t *testing.T) {
	budgets := testBudgets()
	budgets.Execute = time.Millisecond
	result := Run(
		context.Background(), Ref("sandbox-runtime", "timeout"), nil, &struct{}{},
		Definition[struct{}]{
			Execute: func(context.Context, *struct{}) error {
				time.Sleep(5 * time.Millisecond)
				return nil
			},
			Budgets: budgets,
		},
	)
	if result.Status != report.StatusError || result.Reason != "execute: context deadline exceeded" {
		t.Fatalf("result = %+v", result)
	}
}
