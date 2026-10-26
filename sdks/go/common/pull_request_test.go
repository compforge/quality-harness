package common_test

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"

	"github.com/compforge/quality-harness/sdks/go/common"
	"gopkg.in/yaml.v3"
)

func TestPullRequestConformance(t *testing.T) {
	data, err := os.ReadFile("../../../conformance/common/pull-requests.json")
	if err != nil {
		t.Fatal(err)
	}
	var rows []struct {
		PullRequest common.PullRequest `json:"pull_request" yaml:"pull_request"`
		ID          string             `json:"id" yaml:"id"`
	}
	if err := json.Unmarshal(data, &rows); err != nil {
		t.Fatal(err)
	}
	identities := map[string]bool{}
	for i, row := range rows {
		var identity common.Identity = row.PullRequest
		if identity.ID() != row.ID {
			t.Fatalf("row %d: got %s, want %s", i, identity.ID(), row.ID)
		}
		identities[identity.ID()] = true
		if row.PullRequest.SameIdentity(rows[0].PullRequest) != (i < 4) {
			t.Fatalf("row %d: identity includes metadata or omits a key", i)
		}
	}
	if len(identities) != 4 {
		t.Fatal("distinct PR identities collapsed")
	}
	if rows[0].PullRequest.SourceRepository.SameIdentity(rows[0].PullRequest.Repository) {
		t.Fatal("fork source was replaced by target")
	}
	if rows[3].PullRequest.State != "" || rows[3].PullRequest.SourceRepository != nil || rows[2].PullRequest.MergedAt != "" {
		t.Fatal("unknown facts were inferred")
	}
	if rows[1].PullRequest.State != common.PullRequestMerged {
		t.Fatal("merged state lost")
	}
	encoded, err := json.Marshal(rows)
	if err != nil {
		t.Fatal(err)
	}
	var got, want any
	if err := json.Unmarshal(encoded, &got); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(data, &want); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatal("JSON facts changed during roundtrip")
	}
	yamlData, err := yaml.Marshal(rows)
	if err != nil {
		t.Fatal(err)
	}
	roundtrip := rows[:0:0]
	if err := yaml.Unmarshal(yamlData, &roundtrip); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(roundtrip, rows) {
		t.Fatal("YAML facts changed during roundtrip")
	}
}
