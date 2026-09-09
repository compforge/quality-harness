package contract

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestParseDocCarriesNamedSpec(t *testing.T) {
	doc := strings.Join([]string{
		"+spec:id=idle_gc,text=`idle carriers are reclaimed`",
		"+case:id=happy_minimal,desc=`minimal create succeeds`,expect=`200`",
	}, "\n")
	cases, err := parseDoc(doc)
	if err != nil {
		t.Fatal(err)
	}
	if len(cases) != 1 || cases[0].SpecID != "idle_gc" || cases[0].SpecText == "" {
		t.Fatalf("cases = %+v", cases)
	}
}

func TestDiscoverFixture(t *testing.T) {
	cases, err := Discover("testdata/src")
	if err != nil {
		t.Fatal(err)
	}
	if len(cases) != 3 || cases[0].Case.ID != "dup_conv" {
		t.Fatalf("cases = %+v", cases)
	}
}

func TestDiscoverRefsAndCoverage(t *testing.T) {
	root := t.TempDir()
	source := `package e2e
import "github.com/compforge/quality-harness/sdks/go/e2e/caserun"
var _ = caserun.Ref("sandbox-runtime", "happy_minimal")
`
	if err := os.WriteFile(filepath.Join(root, "runtime_test.go"), []byte(source), 0o644); err != nil {
		t.Fatal(err)
	}
	refs, err := DiscoverRefs(root)
	if err != nil {
		t.Fatal(err)
	}
	cases := []DiscoveredCase{
		{Case: Case{ID: "happy_minimal"}},
		{Case: Case{ID: "missing"}},
	}
	coverage := CheckCoverage("sandbox-runtime", cases, refs)
	if coverage.OK() || len(coverage.Missing) != 1 || coverage.Missing[0] != "missing" {
		t.Fatalf("coverage = %+v", coverage)
	}
}

func TestDiscoverRefsRejectsDynamicIdentity(t *testing.T) {
	root := t.TempDir()
	source := `package e2e
import "github.com/compforge/quality-harness/sdks/go/e2e/caserun"
func bind(id string) { _ = caserun.Ref("sandbox-runtime", id) }
`
	if err := os.WriteFile(filepath.Join(root, "runtime_test.go"), []byte(source), 0o644); err != nil {
		t.Fatal(err)
	}
	_, err := DiscoverRefs(root)
	if err == nil || !strings.Contains(err.Error(), "string literals") {
		t.Fatalf("error = %v", err)
	}
}

func TestDiscoverRejectsBrokenSource(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "broken.go"), []byte("package broken\nfunc ("), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := Discover(root); err == nil {
		t.Fatal("expected parse error")
	}
}
