package trajectory

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"testing"
	"time"

	"github.com/compforge/agent-ledger/go/observation"
)

type fixture struct {
	Sessions []observation.SessionView `json:"sessions"`
	Expected struct {
		Global   map[string]map[string]float64            `json:"global"`
		Sessions map[string]map[string]map[string]float64 `json:"sessions"`
	} `json:"expected"`
}

func loadFixture(t *testing.T) fixture {
	t.Helper()
	data, err := os.ReadFile("../../../conformance/trajectory/session-measurements.json")
	if err != nil {
		t.Fatal(err)
	}
	var f fixture
	if err := json.Unmarshal(data, &f); err != nil {
		t.Fatal(err)
	}
	return f
}
func assertValues(t *testing.T, view MeasurementView, expected map[string]map[string]float64) {
	t.Helper()
	for id, values := range expected {
		for name, want := range values {
			found := false
			for _, row := range view.Values {
				if row.MeasurerID == id && row.Name == name {
					found = true
					if row.Value == nil || *row.Value != want {
						t.Fatalf("%s.%s = %v, want %v", id, name, row.Value, want)
					}
				}
			}
			if !found {
				t.Fatalf("missing %s.%s", id, name)
			}
		}
	}
}

// +case=`Python and Go share count, token, failure, zero-duration and aggregation expectations from the same native recording fixture`
// +link=repo://conformance/trajectory/README.md
func TestConformance(t *testing.T) {
	f := loadFixture(t)
	runner := DefaultRunner()
	run, err := runner.Run(context.Background(), append(f.Sessions, f.Sessions[0]))
	if err != nil {
		t.Fatal(err)
	}
	views, err := Project(run, Metadata{RunID: "test", CreatedAt: time.Now()})
	if err != nil {
		t.Fatal(err)
	}
	if len(views.Sessions) != 3 {
		t.Fatal("shared Session counted twice")
	}
	assertValues(t, views.Global, f.Expected.Global)
	for _, view := range views.Sessions {
		assertValues(t, view, f.Expected.Sessions[view.Session.ID])
	}
	for _, row := range views.Global.Values {
		if len(row.Evidence) > 0 || len(row.Explanations) > 0 {
			t.Fatal("Global contains Session detail")
		}
	}
}

type customMeasurer struct {
	version string
	fail    bool
}

func (m customMeasurer) Spec() MeasurerSpec {
	return MeasurerSpec{ID: "new_signal", Version: m.version, Title: "New signal", Category: "cost", Measurements: []MeasurementSpec{field("occurrences", "count", "Custom registered signal.", true)}}
}
func (m customMeasurer) Measure(_ context.Context, s observation.SessionView) (MeasurementResult, error) {
	if m.fail {
		return MeasurementResult{}, errors.New("unavailable")
	}
	values := map[string]float64{}
	if s.Session.ID == "alpha" {
		values["occurrences"] = 0
	}
	return MeasurementResult{MeasurerID: "new_signal", Status: "measured", Measurements: values}, nil
}

// +case=`New plugins automatically project measured zero, missing results and plugin failures without inventing values`
func TestPluginCoverageAndVersion(t *testing.T) {
	f := loadFixture(t)
	runner := Runner{Measurers: []Measurer{customMeasurer{version: "1"}}}
	before, _ := runner.Fingerprint()
	run, err := runner.Run(context.Background(), f.Sessions[:2])
	if err != nil {
		t.Fatal(err)
	}
	view, err := Project(run, Metadata{})
	if err != nil {
		t.Fatal(err)
	}
	row := view.Global.Values[0]
	if row.Value == nil || *row.Value != 0 || row.Status != "partial" || row.Coverage["missing"] != 1 {
		t.Fatalf("coverage: %+v", row)
	}
	runner.Measurers = []Measurer{customMeasurer{version: "2", fail: true}}
	after, _ := runner.Fingerprint()
	if before == after {
		t.Fatal("version omitted from cache key")
	}
	run, err = runner.Run(context.Background(), f.Sessions[:1])
	if err != nil {
		t.Fatal(err)
	}
	view, _ = Project(run, Metadata{})
	if view.Global.Values[0].Status != "error" || view.Global.Values[0].Value != nil {
		t.Fatal("failure manufactured value")
	}
}
func TestConflictingSessionAndCanceledRun(t *testing.T) {
	f := loadFixture(t)
	duplicate := f.Sessions[0]
	duplicate.Nodes = nil
	if _, err := DefaultRunner().Run(context.Background(), []observation.SessionView{f.Sessions[0], duplicate}); err == nil {
		t.Fatal("conflicting duplicate accepted")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := DefaultRunner().Run(ctx, f.Sessions); !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
}
func TestNestedCallsAndUnknownDuration(t *testing.T) {
	f := loadFixture(t)
	session := f.Sessions[0]
	original := session.Nodes
	session.Nodes = observation.Nodes{&observation.Turn{Node: observation.Node{ID: "turn"}, Nodes: original}}
	a, err := DefaultRunner().Run(context.Background(), []observation.SessionView{session})
	if err != nil {
		t.Fatal(err)
	}
	b, err := DefaultRunner().Run(context.Background(), f.Sessions[:1])
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(a, b) {
		t.Fatal("nested calls changed measurements")
	}
	session.Nodes = observation.Nodes{&observation.ToolCall{Node: observation.Node{ID: "tool"}}}
	run, err := DefaultRunner().Run(context.Background(), []observation.SessionView{session})
	if err != nil {
		t.Fatal(err)
	}
	for _, result := range run.Sessions[0].Results {
		if result.MeasurerID == "tool_usage" {
			if _, ok := result.Measurements["tool_duration_ms"]; ok {
				t.Fatal("unknown duration converted to zero")
			}
		}
	}
}
func TestNearestRankPercentiles(t *testing.T) {
	if aggregate([]float64{1, 2, 100, 200}, "p50") != 2 || aggregate([]float64{1, 2, 100, 200}, "p95") != 200 {
		t.Fatal("percentile differs from Python nearest rank")
	}
}

func TestPersistedRunRejectsUnsupportedAggregation(t *testing.T) {
	f := loadFixture(t)
	run, err := DefaultRunner().Run(context.Background(), f.Sessions)
	if err != nil {
		t.Fatal(err)
	}
	run.Catalog[0].Measurements[0].Aggregations = []string{"invented"}
	if _, err := Project(run, Metadata{}); err == nil {
		t.Fatal("unsupported summary silently projected")
	}
}
