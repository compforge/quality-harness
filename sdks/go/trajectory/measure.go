// Package trajectory measures recorded agent behavior without replaying calls.
package trajectory

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"reflect"

	"github.com/compforge/agent-ledger/go/common"
	"github.com/compforge/agent-ledger/go/observation"
)

type MeasurementSpec struct {
	Name               string   `json:"name"`
	Unit               string   `json:"unit"`
	Description        string   `json:"description"`
	Direction          string   `json:"direction"`
	Aggregations       []string `json:"aggregations"`
	SummaryAggregation string   `json:"summary_aggregation,omitempty"`
}

type MeasurerSpec struct {
	ID           string            `json:"measurer_id"`
	Version      string            `json:"version"`
	Title        string            `json:"title"`
	Description  string            `json:"description"`
	Category     string            `json:"category"`
	Owner        string            `json:"owner"`
	Measurements []MeasurementSpec `json:"measurements"`
}

type MeasurementResult struct {
	MeasurerID   string                  `json:"measurer_id"`
	Status       string                  `json:"status"`
	Measurements map[string]float64      `json:"measurements"`
	Explanation  string                  `json:"explanation,omitempty"`
	Evidence     []observation.SourceRef `json:"evidence,omitempty"`
}

type Measurer interface {
	Spec() MeasurerSpec
	Measure(context.Context, observation.SessionView) (MeasurementResult, error)
}

type SessionMeasurement struct {
	Session common.SessionRef   `json:"session"`
	Results []MeasurementResult `json:"results"`
	Issues  []string            `json:"issues,omitempty"`
}

type Run struct {
	Catalog  []MeasurerSpec       `json:"measurer_specs"`
	Sessions []SessionMeasurement `json:"sessions"`
}

type Runner struct{ Measurers []Measurer }

func DefaultRunner() Runner {
	return Runner{Measurers: []Measurer{ModelUsageMeasurer{}, ToolUsageMeasurer{}}}
}

// Fingerprint includes the projection contract and each plugin's explicit semantic version.
func (r Runner) Fingerprint() (string, error) {
	catalog, err := r.catalog()
	if err != nil {
		return "", err
	}
	data, err := json.Marshal(struct {
		Contract string
		Catalog  []MeasurerSpec
	}{"session-measurements@1", catalog})
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:]), nil
}

func (r Runner) catalog() ([]MeasurerSpec, error) {
	catalog := make([]MeasurerSpec, 0, len(r.Measurers))
	for _, m := range r.Measurers {
		catalog = append(catalog, m.Spec())
	}
	return catalog, validateCatalog(catalog)
}

func validateCatalog(catalog []MeasurerSpec) error {
	seen := map[string]bool{}
	for _, s := range catalog {
		if s.ID == "" || s.Version == "" || seen[s.ID] || (s.Category != "cost" && s.Category != "effect") {
			return fmt.Errorf("invalid or duplicate Measurer %q", s.ID)
		}
		seen[s.ID] = true
		names := map[string]bool{}
		for _, v := range s.Measurements {
			if v.Name == "" || names[v.Name] {
				return fmt.Errorf("Measurer %s: invalid or duplicate measurement %q", s.ID, v.Name)
			}
			names[v.Name] = true
			summaryFound := v.SummaryAggregation == ""
			for _, a := range v.Aggregations {
				switch a {
				case "sum", "mean", "p50", "p95":
				default:
					return fmt.Errorf("Measurer %s: unsupported aggregation %q", s.ID, a)
				}
				summaryFound = summaryFound || a == v.SummaryAggregation
			}
			if !summaryFound {
				return fmt.Errorf("Measurer %s: summary aggregation not declared", s.ID)
			}
		}
	}
	return nil
}

// +spec=`Each explicitly identified Session is measured once; conflicting duplicates fail and plugin errors remain health data`
// +link=repo://docs/trajectory-harness.md
func (r Runner) Run(ctx context.Context, sessions []observation.SessionView) (Run, error) {
	catalog, err := r.catalog()
	if err != nil {
		return Run{}, err
	}
	run := Run{Catalog: catalog, Sessions: []SessionMeasurement{}}
	seen := map[common.SessionRef]observation.SessionView{}
	for _, session := range sessions {
		if err := ctx.Err(); err != nil {
			return Run{}, err
		}
		if err := session.Validate(); err != nil {
			return Run{}, fmt.Errorf("Session %s: %w", session.Session.ID, err)
		}
		if previous, ok := seen[session.Session]; ok {
			if !reflect.DeepEqual(previous, session) {
				return Run{}, fmt.Errorf("conflicting Session %s", session.Session.ID)
			}
			continue
		}
		seen[session.Session] = session
		row := SessionMeasurement{Session: session.Session, Issues: session.Issues, Results: []MeasurementResult{}}
		for i, m := range r.Measurers {
			if err := ctx.Err(); err != nil {
				return Run{}, err
			}
			result, err := m.Measure(ctx, session)
			if err == nil {
				err = validateResult(catalog[i], result)
			}
			if err != nil {
				result = MeasurementResult{MeasurerID: catalog[i].ID, Status: "error", Explanation: err.Error()}
			}
			row.Results = append(row.Results, result)
		}
		run.Sessions = append(run.Sessions, row)
	}
	return run, ctx.Err()
}

func validateResult(spec MeasurerSpec, result MeasurementResult) error {
	if result.MeasurerID != spec.ID {
		return fmt.Errorf("Measurer %s returned identity %q", spec.ID, result.MeasurerID)
	}
	switch result.Status {
	case "measured", "error", "not_applicable":
	default:
		return fmt.Errorf("Measurer %s returned status %q", spec.ID, result.Status)
	}
	declared := map[string]bool{}
	for _, v := range spec.Measurements {
		declared[v.Name] = true
	}
	for name, value := range result.Measurements {
		if !declared[name] || math.IsNaN(value) || math.IsInf(value, 0) {
			return fmt.Errorf("Measurer %s returned invalid measurement %s", spec.ID, name)
		}
	}
	return nil
}
