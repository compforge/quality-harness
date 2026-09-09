// Package contract checks that source-level +case intent is represented by an
// executable CaseRun. It performs static Go AST analysis and never imports the
// service under test.
package contract

import (
	"fmt"
	"strings"

	"github.com/compforge/spec-case/toolchains/go/marker"
)

// Case is one natural-language marker bound to its source symbol and optional
// named spec. Canonical executable identity is CaseSet + ID; Group remains only
// authoring metadata from spec-case.
type Case struct {
	ID       string
	Desc     string
	Input    string
	Expect   string
	Forbid   string
	Group    string
	Symbol   string
	SpecID   string
	SpecText string
}

func parseDoc(doc string) ([]Case, error) {
	parsed := marker.Parse(doc)
	for lineNo, raw := range strings.Split(doc, "\n") {
		line := strings.TrimSpace(raw)
		if !strings.HasPrefix(line, "+case:") {
			continue
		}
		if len(marker.Parse(line).Cases) != 1 {
			return nil, fmt.Errorf("invalid +case marker on doc line %d: %s", lineNo+1, line)
		}
	}
	cases := make([]Case, 0, len(parsed.Cases))
	for _, item := range parsed.Cases {
		cases = append(cases, Case{
			ID: item.ID, Desc: item.Desc, Input: item.Input,
			Expect: item.Expect, Forbid: item.Forbid, Group: item.Group,
			SpecID: parsed.SpecID, SpecText: parsed.Spec,
		})
	}
	return cases, nil
}
