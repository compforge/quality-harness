package runner

import (
	"encoding/json"
	"testing"
)

func TestOutcomeField(t *testing.T) {
	body := `{"data":{"id":"abc","items":[1,2,3]}}`
	o := &Outcome{Body: json.RawMessage(body)}

	tests := []struct {
		path string
		want string
	}{
		{"data.id", "abc"},
		{"data.items.0", "1"},
		{"data.items.2", "3"},
	}
	for _, tc := range tests {
		got := o.FieldStr(tc.path)
		if got != tc.want {
			t.Errorf("Field(%q) = %q, want %q", tc.path, got, tc.want)
		}
	}

	if got := o.Field("missing.path"); got != nil {
		t.Errorf("Field(missing.path) = %v, want nil", got)
	}
}

func TestOutcomeBodyMap(t *testing.T) {
	body := `{"name":"hello","count":42}`
	o := &Outcome{Body: json.RawMessage(body)}

	m := o.BodyMap()
	if m == nil {
		t.Fatal("BodyMap returned nil")
	}
	if m["name"] != "hello" {
		t.Errorf("name = %v, want %q", m["name"], "hello")
	}
}

func TestOutcomeNilBody(t *testing.T) {
	o := &Outcome{}
	if got := o.Field("any.path"); got != nil {
		t.Errorf("expected nil for nil body, got %v", got)
	}
	if got := o.FieldStr("any.path"); got != "" {
		t.Errorf("expected empty string for nil body, got %q", got)
	}
}
