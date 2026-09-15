// Package environment binds reusable E2E cases to named execution conditions.
package environment

import (
	"context"
	"fmt"
	"maps"
	"sort"

	"github.com/compforge/quality-harness/sdks/go/common"
	"github.com/compforge/quality-harness/sdks/go/e2e/caserun"
	"github.com/compforge/quality-harness/sdks/go/e2e/matrix"
)

// State is passed to each existing CaseRun phase. Value belongs to the project;
// Environment is populated by its fixture, including after a partial failure.
type State[S any] struct {
	Environment common.EnvironmentSnapshot
	Value       S
}

// Run reuses CaseRun's deadlines and unconditional cleanup. Required facts are
// checked after preparation, before any stimulus. Missing facts are errors,
// not skipped tests or a reason to weaken the expected product behavior.
func Run[S any](ctx context.Context, ref caserun.CaseRef, variant matrix.Variant, state *State[S], required map[string]string, def caserun.Definition[State[S]]) caserun.Result {
	prepare := def.Prepare
	stateName, stateProfile, stateKind := state.Environment.Name, state.Environment.Profile, state.Environment.Kind
	def.Prepare = func(ctx context.Context, s *State[S]) error {
		if s.Environment.Name == "" || s.Environment.Kind == "" || s.Environment.Profile == "" {
			return fmt.Errorf("environment name, kind and profile are required")
		}
		if prepare != nil {
			if err := prepare(ctx, s); err != nil {
				return err
			}
		}
		if s.Environment.Name != stateName || s.Environment.Profile != stateProfile || s.Environment.Kind != stateKind {
			return fmt.Errorf("prepare changed environment identity")
		}
		if s.Environment.Target.Source == "" || s.Environment.Target.ObservedAt == "" {
			return fmt.Errorf("target environment facts require source and observed_at")
		}
		keys := make([]string, 0, len(required))
		for key := range required {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		for _, key := range keys {
			actual, ok := s.Environment.Target.Values[key]
			if !ok || actual != required[key] {
				return fmt.Errorf("environment condition %s: observed %q (known=%t), required %q", key, actual, ok, required[key])
			}
		}
		return nil
	}
	bound := maps.Clone(variant)
	if bound == nil {
		bound = matrix.Variant{}
	}
	for key, value := range map[string]string{"environment": state.Environment.Name, "profile": state.Environment.Profile} {
		if old, exists := bound[key]; exists && old != value {
			def.Prepare = func(context.Context, *State[S]) error {
				return fmt.Errorf("variant %s conflicts with selected environment", key)
			}
		}
		bound[key] = value
	}
	result := caserun.Run(ctx, ref, bound, state, def)
	// Results must survive fixture reuse without later mutations rewriting evidence.
	snapshot := state.Environment
	snapshot.Runner.Values = maps.Clone(snapshot.Runner.Values)
	snapshot.Target.Values = maps.Clone(snapshot.Target.Values)
	if snapshot.Host != nil {
		host := *snapshot.Host
		host.Values = maps.Clone(host.Values)
		snapshot.Host = &host
	}
	result.Environment = &snapshot
	return result
}
