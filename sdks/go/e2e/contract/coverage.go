package contract

import (
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"io/fs"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
)

type CaseRef struct {
	CaseSet    string
	ID         string
	SourceFile string
	Line       int
}

type Coverage struct {
	Missing   []string
	Orphaned  []string
	Duplicate []string
}

func (coverage Coverage) OK() bool {
	return len(coverage.Missing) == 0 && len(coverage.Orphaned) == 0 && len(coverage.Duplicate) == 0
}

// DiscoverRefs indexes literal caserun.Ref("caseset", "case_id") calls. A
// dynamic argument is rejected because a CI coverage gate must be decidable
// without compiling or executing the test repository.
func DiscoverRefs(testRoot string) ([]CaseRef, error) {
	var refs []CaseRef
	fset := token.NewFileSet()
	err := filepath.WalkDir(testRoot, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() {
			if path != testRoot && skipDir(entry.Name()) {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(path, ".go") {
			return nil
		}
		file, err := parser.ParseFile(fset, path, nil, parser.SkipObjectResolution)
		if err != nil {
			return fmt.Errorf("parse test source %s: %w", path, err)
		}
		rel, err := filepath.Rel(testRoot, path)
		if err != nil {
			return err
		}
		var scanErr error
		ast.Inspect(file, func(node ast.Node) bool {
			call, ok := node.(*ast.CallExpr)
			if !ok || !isCaseRefCall(call.Fun) {
				return true
			}
			if len(call.Args) != 2 {
				scanErr = fmt.Errorf("%s:%d: caserun.Ref requires caseset and case id literals", rel, fset.Position(call.Pos()).Line)
				return false
			}
			caseset, casesetOK := stringLiteral(call.Args[0])
			caseID, caseIDOK := stringLiteral(call.Args[1])
			if !casesetOK || !caseIDOK || caseset == "" || caseID == "" {
				scanErr = fmt.Errorf("%s:%d: caserun.Ref arguments must be non-empty string literals", rel, fset.Position(call.Pos()).Line)
				return false
			}
			refs = append(refs, CaseRef{
				CaseSet: caseset, ID: caseID, SourceFile: filepath.ToSlash(rel),
				Line: fset.Position(call.Pos()).Line,
			})
			return true
		})
		return scanErr
	})
	if err != nil {
		return nil, err
	}
	sort.Slice(refs, func(i, j int) bool {
		if refs[i].CaseSet != refs[j].CaseSet {
			return refs[i].CaseSet < refs[j].CaseSet
		}
		return refs[i].ID < refs[j].ID
	})
	return refs, nil
}

func CheckCoverage(caseset string, cases []DiscoveredCase, refs []CaseRef) Coverage {
	declared := make(map[string]bool, len(cases))
	for _, item := range cases {
		declared[item.Case.ID] = true
	}
	implemented := map[string]int{}
	for _, ref := range refs {
		if ref.CaseSet == caseset {
			implemented[ref.ID]++
		}
	}
	coverage := Coverage{}
	for id := range declared {
		if implemented[id] == 0 {
			coverage.Missing = append(coverage.Missing, id)
		}
	}
	for id, count := range implemented {
		if !declared[id] {
			coverage.Orphaned = append(coverage.Orphaned, id)
		}
		if count > 1 {
			coverage.Duplicate = append(coverage.Duplicate, id)
		}
	}
	sort.Strings(coverage.Missing)
	sort.Strings(coverage.Orphaned)
	sort.Strings(coverage.Duplicate)
	return coverage
}

func isCaseRefCall(expression ast.Expr) bool {
	selector, ok := expression.(*ast.SelectorExpr)
	if !ok || selector.Sel.Name != "Ref" {
		return false
	}
	identifier, ok := selector.X.(*ast.Ident)
	return ok && identifier.Name == "caserun"
}

func stringLiteral(expression ast.Expr) (string, bool) {
	literal, ok := expression.(*ast.BasicLit)
	if !ok || literal.Kind != token.STRING {
		return "", false
	}
	value, err := strconv.Unquote(literal.Value)
	return value, err == nil
}
