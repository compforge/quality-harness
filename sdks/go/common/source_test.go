package common_test

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"

	"github.com/compforge/quality-harness/sdks/go/common"
	"gopkg.in/yaml.v3"
)

func TestSourceIdentityConformance(t *testing.T) {
	data, err := os.ReadFile("../../../conformance/common/source-identities.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Products     []common.Product    `json:"products" yaml:"products"`
		Repositories []common.Repository `json:"repositories" yaml:"repositories"`
		Components   []common.Component  `json:"components" yaml:"components"`
	}
	if err := json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	components := map[common.Component]bool{}
	for _, component := range fixture.Components {
		components[component] = true
	}
	if len(components) != 3 {
		t.Fatalf("forge/repository identities collapsed: %#v", components)
	}
	described := fixture.Components[0]
	described.Description = "A different description"
	if !described.SameIdentity(fixture.Components[0]) || described.SameIdentity(fixture.Components[1]) {
		t.Fatal("description changed identity or forge identity was ignored")
	}
	encoded, err := json.Marshal(fixture)
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
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("wire contract mismatch: %s", encoded)
	}
	yamlData, err := yaml.Marshal(fixture)
	if err != nil {
		t.Fatal(err)
	}
	roundtrip := fixture
	roundtrip.Components = nil
	if err := yaml.Unmarshal(yamlData, &roundtrip); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(roundtrip, fixture) {
		t.Fatalf("YAML roundtrip mismatch: %s", yamlData)
	}
}
