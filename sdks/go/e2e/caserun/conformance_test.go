package caserun

import (
	"context"
	"errors"
	"maps"
	"os"
	"reflect"
	"testing"
	"time"

	"github.com/compforge/quality-harness/sdks/go/e2e/matrix"
	"github.com/compforge/quality-harness/sdks/go/report"
	"gopkg.in/yaml.v3"
)

type lifecycleFixture struct {
	Cases   []lifecycleCase   `yaml:"cases"`
	Rollups []lifecycleRollup `yaml:"rollups"`
}

type lifecycleCase struct {
	ID      string            `yaml:"id"`
	Steps   map[string]string `yaml:"steps"`
	Variant map[string]string `yaml:"variant"`
	Facets  map[string]string `yaml:"facets"`
	Want    lifecycleWant     `yaml:"want"`
}

type lifecycleWant struct {
	Status      report.Status           `yaml:"status"`
	Phases      []Phase                 `yaml:"phases"`
	PhaseStatus map[Phase]report.Status `yaml:"phase_status"`
	ArmID       string                  `yaml:"arm_id"`
	Facets      map[string]string       `yaml:"facets"`
}

type lifecycleRollup struct {
	ID       string          `yaml:"id"`
	Statuses []report.Status `yaml:"statuses"`
	Want     report.Status   `yaml:"want"`
}

func TestLifecycleConformance(t *testing.T) {
	fixture := loadLifecycleFixture(t)
	for _, item := range fixture.Cases {
		t.Run(item.ID, func(t *testing.T) {
			result := Run(
				context.Background(), Ref("e2e-conformance", item.ID), matrix.Variant(item.Variant), &struct{}{},
				Definition[struct{}]{
					Prepare: conformanceStep(item.Steps["prepare"]),
					Execute: conformanceStep(item.Steps["execute"]),
					Judge:   conformanceStep(item.Steps["judge"]),
					Cleanup: conformanceStep(item.Steps["cleanup"]),
					Budgets: Budgets{
						Prepare: time.Second, Execute: time.Second, Judge: time.Second, Cleanup: time.Second,
					},
					Facets: item.Facets,
				},
			)
			if result.Status != item.Want.Status {
				t.Fatalf("status = %q, want %q: %+v", result.Status, item.Want.Status, result)
			}
			phases := make([]Phase, len(result.Phases))
			for index, phase := range result.Phases {
				phases[index] = phase.Phase
				if want, ok := item.Want.PhaseStatus[phase.Phase]; ok && phase.Status != want {
					t.Errorf("phase %s status = %q, want %q", phase.Phase, phase.Status, want)
				}
			}
			if !reflect.DeepEqual(phases, item.Want.Phases) {
				t.Errorf("phases = %v, want %v", phases, item.Want.Phases)
			}
			verdict := result.CaseVerdict()
			if verdict.ArmID != item.Want.ArmID || !maps.Equal(verdict.Facets, item.Want.Facets) {
				t.Errorf("identity = arm %q facets %v, want arm %q facets %v", verdict.ArmID, verdict.Facets, item.Want.ArmID, item.Want.Facets)
			}
		})
	}
}

func TestVerdictRollupConformance(t *testing.T) {
	fixture := loadLifecycleFixture(t)
	for _, item := range fixture.Rollups {
		t.Run(item.ID, func(t *testing.T) {
			cases := make([]report.CaseVerdict, len(item.Statuses))
			for index, status := range item.Statuses {
				cases[index] = report.CaseVerdict{CaseID: item.ID, ArmID: string(rune('a' + index)), Status: status}
			}
			verdict := report.BuildRunVerdict("e2e-conformance", item.ID, cases)
			if verdict.Status != item.Want {
				t.Fatalf("rollup status = %q, want %q", verdict.Status, item.Want)
			}
		})
	}
}

func conformanceStep(action string) Step[struct{}] {
	if action == "" {
		return nil
	}
	return func(context.Context, *struct{}) error {
		switch action {
		case "pass":
			return nil
		case "error":
			return errors.New("fixture error")
		case "fail":
			return Fail("fixture mismatch")
		case "skip":
			return Skip("fixture unavailable")
		default:
			return errors.New("unknown fixture action: " + action)
		}
	}
}

func loadLifecycleFixture(t *testing.T) lifecycleFixture {
	t.Helper()
	data, err := os.ReadFile("../../../../conformance/e2e/caserun.yaml")
	if err != nil {
		t.Fatal(err)
	}
	var fixture lifecycleFixture
	if err := yaml.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	return fixture
}
