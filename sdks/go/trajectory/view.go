package trajectory

import (
	"fmt"
	"math"
	"sort"
	"strconv"
	"time"

	"github.com/compforge/agent-ledger/go/common"
	"github.com/compforge/agent-ledger/go/observation"
)

type ViewValue struct {
	MeasurerID    string                  `json:"measurer_id"`
	MeasurerTitle string                  `json:"measurer_title"`
	Category      string                  `json:"category"`
	Name          string                  `json:"name"`
	Description   string                  `json:"description"`
	Unit          string                  `json:"unit"`
	Direction     string                  `json:"direction"`
	Aggregation   *string                 `json:"aggregation"`
	Value         *float64                `json:"value"`
	Status        string                  `json:"status"`
	Coverage      map[string]int          `json:"coverage"`
	Evidence      []observation.SourceRef `json:"evidence,omitempty"`
	Explanations  []string                `json:"explanations,omitempty"`
}

type MeasurementView struct {
	Session *common.SessionRef `json:"session,omitempty"`
	Values  []ViewValue        `json:"values"`
}

type Views struct {
	Schema         int               `json:"schema"`
	RunID          string            `json:"run_id"`
	DatasetID      string            `json:"dataset_id"`
	DatasetVersion string            `json:"dataset_version"`
	CreatedAt      time.Time         `json:"created_at"`
	Catalog        []MeasurerSpec    `json:"measurer_specs"`
	Global         MeasurementView   `json:"global"`
	Sessions       []MeasurementView `json:"sessions"`
	Unassigned     []string          `json:"unassigned_trajectories"`
}

type Metadata struct {
	RunID, DatasetID, DatasetVersion string
	CreatedAt                        time.Time
}

// Project renders recorded results only. Global intentionally omits per-Session evidence and distribution.
func Project(run Run, metadata Metadata) (Views, error) {
	if err := validateCatalog(run.Catalog); err != nil {
		return Views{}, err
	}
	specs := map[string]MeasurerSpec{}
	for _, spec := range run.Catalog {
		specs[spec.ID] = spec
	}
	seen := map[common.SessionRef]bool{}
	for _, session := range run.Sessions {
		if session.Session.Source == "" || session.Session.ID == "" {
			return Views{}, fmt.Errorf("measurement Run has no Session identity")
		}
		if seen[session.Session] {
			return Views{}, fmt.Errorf("duplicate Session %s in measurement Run", session.Session.ID)
		}
		seen[session.Session] = true
		results := map[string]bool{}
		for _, result := range session.Results {
			if results[result.MeasurerID] {
				return Views{}, fmt.Errorf("duplicate Measurer %s in Session %s", result.MeasurerID, session.Session.ID)
			}
			spec, ok := specs[result.MeasurerID]
			if !ok {
				return Views{}, fmt.Errorf("unknown Measurer %s in measurement Run", result.MeasurerID)
			}
			if err := validateResult(spec, result); err != nil {
				return Views{}, err
			}
			results[result.MeasurerID] = true
		}
	}
	views := Views{Schema: 1, RunID: metadata.RunID, DatasetID: metadata.DatasetID, DatasetVersion: metadata.DatasetVersion, CreatedAt: metadata.CreatedAt, Catalog: run.Catalog, Global: project(run.Catalog, run.Sessions, nil), Sessions: []MeasurementView{}, Unassigned: []string{}}
	for _, session := range run.Sessions {
		ref := session.Session
		views.Sessions = append(views.Sessions, project(run.Catalog, []SessionMeasurement{session}, &ref))
	}
	return views, nil
}

func project(catalog []MeasurerSpec, sessions []SessionMeasurement, ref *common.SessionRef) MeasurementView {
	view := MeasurementView{Session: ref, Values: []ViewValue{}}
	for _, spec := range catalog {
		for _, definition := range spec.Measurements {
			row := ViewValue{MeasurerID: spec.ID, MeasurerTitle: spec.Title, Category: spec.Category, Name: definition.Name, Description: definition.Description, Unit: definition.Unit, Direction: definition.Direction, Status: "missing", Coverage: map[string]int{"measured": 0, "missing": 0, "error": 0, "not_applicable": 0}}
			aggregation := definition.SummaryAggregation
			if aggregation == "" && len(definition.Aggregations) > 0 {
				aggregation = definition.Aggregations[0]
			}
			if aggregation != "" {
				row.Aggregation = &aggregation
			}
			values := []float64{}
			incomplete := false
			for _, session := range sessions {
				status := "missing"
				for _, result := range session.Results {
					if result.MeasurerID == spec.ID {
						status = result.Status
						if status == "measured" {
							if value, ok := result.Measurements[definition.Name]; ok {
								values = append(values, value)
							} else {
								status = "missing"
							}
						}
						if ref != nil {
							row.Explanations = append(row.Explanations, result.Explanation)
							if status == "measured" {
								row.Evidence = append(row.Evidence, result.Evidence...)
							}
						}
						break
					}
				}
				row.Coverage[status]++
				incomplete = incomplete || len(session.Issues) > 0
			}
			switch {
			case len(values) > 0:
				row.Status = "measured"
				if incomplete || row.Coverage["missing"] > 0 || row.Coverage["error"] > 0 {
					row.Status = "partial"
				}
				if aggregation == "" {
					row.Status = "not_aggregated"
				} else {
					value := aggregate(values, aggregation)
					row.Value = &value
				}
			case row.Coverage["error"] > 0:
				row.Status = "error"
			case len(sessions) > 0 && row.Coverage["not_applicable"] == len(sessions):
				row.Status = "not_applicable"
			}
			view.Values = append(view.Values, row)
		}
	}
	return view
}

func aggregate(values []float64, operation string) float64 {
	total := 0.0
	for _, v := range values {
		total += v
	}
	switch operation {
	case "sum":
		return round(total)
	case "mean":
		return round(total / float64(len(values)))
	default:
		sort.Float64s(values)
		q := .5
		if operation == "p95" {
			q = .95
		}
		return round(values[int(math.Ceil(q*float64(len(values))))-1])
	}
}
func round(value float64) float64 {
	// Decimal formatting rounds the original float, matching Python round(x, 6)
	// without introducing a second rounding through multiplication by 1e6.
	rounded, _ := strconv.ParseFloat(strconv.FormatFloat(value, 'f', 6, 64), 64)
	return rounded
}
