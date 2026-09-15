package environment

import (
	"context"
	"errors"
	"os"
	"reflect"
	"testing"
	"time"

	"github.com/compforge/quality-harness/sdks/go/common"
	"github.com/compforge/quality-harness/sdks/go/e2e/caserun"
	"gopkg.in/yaml.v3"
)

func TestEnvironmentConformance(t *testing.T) {
	data, err := os.ReadFile("../../../../conformance/e2e/environments.yaml")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Environments []struct {
			Spec common.Environment
			Kind string
		}
		Invalid   []common.Environment
		Scenarios []struct {
			Name               string
			Observed, Required map[string]string
			Failure, Status    string
			Steps              []string
		}
	}
	if err := yaml.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	for _, entry := range fixture.Environments {
		if err := entry.Spec.Validate(); err != nil || entry.Spec.ResolvedKind() != entry.Kind {
			t.Fatalf("%+v: %v", entry, err)
		}
	}
	for _, entry := range fixture.Invalid {
		if entry.Validate() == nil {
			t.Fatalf("accepted %+v", entry)
		}
	}
	for _, scenario := range fixture.Scenarios {
		t.Run(scenario.Name, func(t *testing.T) {
			var steps []string
			state := State[int]{Environment: common.EnvironmentSnapshot{Name: "devbox-k8s", Kind: "kubernetes", Profile: scenario.Name}}
			step := func(name string) caserun.Step[State[int]] {
				return func(_ context.Context, s *State[int]) error {
					steps = append(steps, name)
					if name == "prepare" {
						s.Environment.Target = common.EnvironmentFacts{Source: "target-probe", ObservedAt: time.Now().UTC().Format(time.RFC3339), Values: scenario.Observed}
					}
					if scenario.Failure == name {
						if name == "judge" {
							return caserun.Fail("wrong product behavior")
						}
						return errors.New("cleanup failed")
					}
					return nil
				}
			}
			result := Run(context.Background(), caserun.Ref("environment", "contract"), nil, &state, scenario.Required, caserun.Definition[State[int]]{Prepare: step("prepare"), Execute: step("execute"), Judge: step("judge"), Cleanup: step("cleanup"), Budgets: caserun.Budgets{Prepare: time.Second, Execute: time.Second, Judge: time.Second, Cleanup: time.Second}})
			if string(result.Status) != scenario.Status || !reflect.DeepEqual(steps, scenario.Steps) {
				t.Fatalf("result=%+v steps=%v", result, steps)
			}
			if result.Variant["environment"] != "devbox-k8s" || result.Environment.Target.Source != "target-probe" {
				t.Fatal("environment evidence missing")
			}
			state.Environment.Target.Values["later"] = "mutation"
			if _, exists := result.Environment.Target.Values["later"]; exists {
				t.Fatal("evidence mutated after run")
			}
		})
	}
}
