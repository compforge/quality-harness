// Package caserun executes one canonical case through its full lifecycle.
package caserun

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"sync"
	"time"

	"github.com/compforge/quality-harness/sdks/go/e2e/matrix"
	"github.com/compforge/quality-harness/sdks/go/report"
)

type CaseRef struct {
	CaseSet string
	ID      string
}

// Ref binds executable code to a case in a canonical CaseSet. casegen
// statically indexes calls whose arguments are string literals.
func Ref(caseset, id string) CaseRef {
	return CaseRef{CaseSet: caseset, ID: id}
}

type Phase string

const (
	PhasePrepare Phase = "prepare"
	PhaseExecute Phase = "execute"
	PhaseJudge   Phase = "judge"
	PhaseCleanup Phase = "cleanup"
)

type Budgets struct {
	Prepare time.Duration
	Execute time.Duration
	Judge   time.Duration
	Cleanup time.Duration
}

type Step[S any] func(context.Context, *S) error

type Definition[S any] struct {
	Prepare Step[S]
	Execute Step[S]
	Judge   Step[S]
	Cleanup Step[S]
	Budgets Budgets
	Facets  map[string]string
}

type PhaseResult struct {
	Phase      Phase         `json:"phase"`
	Status     report.Status `json:"status"`
	StartedAt  time.Time     `json:"started_at"`
	FinishedAt time.Time     `json:"finished_at"`
	Duration   time.Duration `json:"duration"`
	Reason     string        `json:"reason,omitempty"`
}

type Result struct {
	Ref     CaseRef
	Variant matrix.Variant
	Status  report.Status
	Reason  string
	Phases  []PhaseResult
	Facets  map[string]string
}

type skipError struct{ reason string }

func (e skipError) Error() string { return e.reason }

// Skip marks a case as inapplicable to the current environment. The cleanup
// phase still runs when it was configured.
func Skip(reason string) error { return skipError{reason: reason} }

type failError struct{ reason string }

func (e failError) Error() string { return e.reason }

// Fail marks a judged behavior mismatch. Operational failures should be
// returned as ordinary errors so the verdict remains untrustworthy/error.
func Fail(reason string) error { return failError{reason: reason} }

func Run[S any](ctx context.Context, ref CaseRef, variant matrix.Variant, state *S, def Definition[S]) Result {
	result := Result{Ref: ref, Variant: variant, Status: report.StatusPass}
	result.Facets = mergeFacets(def.Facets, variant)
	if ref.CaseSet == "" || ref.ID == "" {
		result.Status = report.StatusError
		result.Reason = "case ref requires non-empty caseset and id"
		return result
	}
	if def.Execute == nil {
		result.Status = report.StatusError
		result.Reason = "case run requires an execute phase"
		return result
	}

	blocked := false
	if def.Prepare != nil {
		phase := runPhase(ctx, PhasePrepare, def.Budgets.Prepare, state, def.Prepare)
		result.Phases = append(result.Phases, phase)
		blocked = applyPhase(&result, phase)
	}
	if !blocked {
		phase := runPhase(ctx, PhaseExecute, def.Budgets.Execute, state, def.Execute)
		result.Phases = append(result.Phases, phase)
		blocked = applyPhase(&result, phase)
	}
	if def.Judge != nil {
		if blocked {
			result.Phases = append(result.Phases, PhaseResult{
				Phase: PhaseJudge, Status: report.StatusSkipped, Reason: "blocked by an earlier phase",
			})
		} else {
			phase := runPhase(ctx, PhaseJudge, def.Budgets.Judge, state, def.Judge)
			result.Phases = append(result.Phases, phase)
			applyPhase(&result, phase)
		}
	}
	if def.Cleanup != nil {
		// Cleanup must not inherit an expired execute deadline. Preserve context
		// values while giving teardown its own explicit budget.
		cleanupBase := context.WithoutCancel(ctx)
		phase := runPhase(cleanupBase, PhaseCleanup, def.Budgets.Cleanup, state, def.Cleanup)
		result.Phases = append(result.Phases, phase)
		applyPhase(&result, phase)
	}
	return result
}

func runPhase[S any](parent context.Context, phase Phase, budget time.Duration, state *S, step Step[S]) (result PhaseResult) {
	startedAt := time.Now()
	result = PhaseResult{Phase: phase, Status: report.StatusPass, StartedAt: startedAt}
	defer func() {
		if recovered := recover(); recovered != nil {
			result.Status = report.StatusError
			result.Reason = fmt.Sprintf("panic: %v", recovered)
		}
		result.FinishedAt = time.Now()
		result.Duration = time.Since(startedAt)
	}()
	if budget <= 0 {
		result.Status = report.StatusError
		result.Reason = "phase requires a positive time budget"
		return result
	}
	ctx, cancel := context.WithTimeout(parent, budget)
	defer cancel()
	if err := step(ctx, state); err != nil {
		var skipped skipError
		var failed failError
		if errors.As(err, &skipped) {
			result.Status = report.StatusSkipped
		} else if errors.As(err, &failed) {
			result.Status = report.StatusFail
		} else {
			result.Status = report.StatusError
		}
		result.Reason = err.Error()
	} else if err := ctx.Err(); err != nil {
		result.Status = report.StatusError
		result.Reason = err.Error()
	}
	return result
}

func applyPhase(result *Result, phase PhaseResult) bool {
	if phase.Status == report.StatusPass {
		return false
	}
	status := phase.Status
	if status == report.StatusSkipped && result.Status != report.StatusError && result.Status != report.StatusFail {
		result.Status = report.StatusSkipped
	} else if status == report.StatusFail && result.Status != report.StatusError {
		result.Status = report.StatusFail
	} else if status == report.StatusError {
		result.Status = report.StatusError
	}
	if result.Reason == "" || status == report.StatusError {
		result.Reason = fmt.Sprintf("%s: %s", phase.Phase, phase.Reason)
	}
	return phase.Phase != PhaseCleanup
}

func mergeFacets(base map[string]string, variant matrix.Variant) map[string]string {
	out := make(map[string]string, len(base)+len(variant))
	for key, value := range base {
		out[key] = value
	}
	for key, value := range variant {
		out[key] = value
	}
	return out
}

func (r Result) CaseVerdict() report.CaseVerdict {
	metrics := make(map[string]report.Metric, len(r.Phases))
	for _, phase := range r.Phases {
		metrics[string(phase.Phase)+"_duration_ms"] = report.Metric{Value: phase.Duration.Milliseconds(), Unit: "ms"}
	}
	return report.CaseVerdict{
		CaseID: r.Ref.ID, ArmID: r.Variant.ID(), Status: r.Status,
		Reason: r.Reason, Facets: r.Facets, Metrics: metrics,
	}
}

// Recorder collects case results across subtests and projects them into one
// run verdict. It is safe for parallel subtests.
type Recorder struct {
	mu      sync.Mutex
	results []report.CaseVerdict
}

func (r *Recorder) Record(result Result) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.results = append(r.results, result.CaseVerdict())
}

func (r *Recorder) Verdict(scope, runID string) report.RunVerdict {
	r.mu.Lock()
	defer r.mu.Unlock()
	cases := append([]report.CaseVerdict(nil), r.results...)
	sort.Slice(cases, func(i, j int) bool {
		if cases[i].CaseID != cases[j].CaseID {
			return cases[i].CaseID < cases[j].CaseID
		}
		return cases[i].ArmID < cases[j].ArmID
	})
	return report.BuildRunVerdict(scope, runID, cases)
}
