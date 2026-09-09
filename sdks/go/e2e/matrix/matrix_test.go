package matrix

import (
	"reflect"
	"testing"
)

func TestExpandIsDeterministicCartesianProduct(t *testing.T) {
	got, err := Expand(map[string][]string{
		"runtime":  {"pod", "bed"},
		"executor": {"supervisor", "worker"},
	})
	if err != nil {
		t.Fatal(err)
	}
	want := []Variant{
		{"executor": "supervisor", "runtime": "pod"},
		{"executor": "supervisor", "runtime": "bed"},
		{"executor": "worker", "runtime": "pod"},
		{"executor": "worker", "runtime": "bed"},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("variants = %#v", got)
	}
	if got[0].ID() != "executor=supervisor,runtime=pod" {
		t.Fatalf("variant id = %q", got[0].ID())
	}
}
