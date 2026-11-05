package common_test

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"

	"github.com/compforge/quality-harness/sdks/go/common"
	"gopkg.in/yaml.v3"
)

// +case=`Optional tools and depot preserve legacy wire values, identity and shared locations`
func TestComponentDepotConformance(t *testing.T) {
	data, err := os.ReadFile("../../../conformance/common/component-depots.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Case      string           `json:"case" yaml:"case"`
		Component common.Component `json:"component" yaml:"component"`
	}
	if err := json.Unmarshal(data, &cases); err != nil {
		t.Fatal(err)
	}
	encoded, err := json.Marshal(cases)
	if err != nil {
		t.Fatal(err)
	}
	var want, got any
	if err := json.Unmarshal(data, &want); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(encoded, &got); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(want, got) {
		t.Fatalf("wire mismatch: %s", encoded)
	}
	yamlData, err := yaml.Marshal(cases)
	if err != nil {
		t.Fatal(err)
	}
	roundtrip := cases[:0:0]
	if err := yaml.Unmarshal(yamlData, &roundtrip); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(cases, roundtrip) {
		t.Fatalf("YAML mismatch: %s", yamlData)
	}

	bare, node, python, shared, empty := cases[0].Component, cases[1].Component, cases[2].Component, cases[3].Component, cases[4].Component
	if bare.Tools != nil || bare.Depot != nil {
		t.Fatal("omitted declarations must stay nil")
	}
	if node.Tools == nil || node.Depot == nil || empty.Tools == nil || empty.Depot == nil {
		t.Fatal("declarations were lost")
	}
	if !node.SameIdentity(bare) || node.SameIdentity(shared) {
		t.Fatal("declarations changed Component identity")
	}
	if !node.SameIdentity(roundtrip[1].Component) {
		t.Fatal("independently decoded metadata changed identity")
	}
	// Component remains usable as a Go map key; metadata pointers are not identity keys.
	comparable := map[common.Component]bool{bare: true}
	if !comparable[bare] {
		t.Fatal("legacy Component key was lost")
	}
	if len(*empty.Tools) != 0 || len(empty.Depot.Bins) != 0 {
		t.Fatal("empty declarations were lost")
	}
	if (*node.Tools)[0].Version != ">=9 <10" || (*node.Tools)[1].Name != "vite" {
		t.Fatal("tool declaration was lost")
	}
	if !reflect.DeepEqual(node.Depot.Bins[0], shared.Depot.Bins[0]) {
		t.Fatal("shared location changed across Components")
	}
	if python.Depot.Bins[0].Path != ".venv" {
		t.Fatal("Python dependency location was lost")
	}
}
