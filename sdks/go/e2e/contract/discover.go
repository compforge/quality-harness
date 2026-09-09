package contract

import (
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"io/fs"
	"path/filepath"
	"sort"
	"strings"
)

type DiscoveredCase struct {
	Case       Case
	SourceFile string
}

// Discover returns every +case marker under sourceRoot. Case IDs must be
// unique because a canonical CaseSet uses id, not source layout, as its key.
func Discover(sourceRoot string) ([]DiscoveredCase, error) {
	var out []DiscoveredCase
	fset := token.NewFileSet()
	err := filepath.WalkDir(sourceRoot, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() {
			if path != sourceRoot && skipDir(entry.Name()) {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		file, err := parser.ParseFile(fset, path, nil, parser.ParseComments|parser.SkipObjectResolution)
		if err != nil {
			return fmt.Errorf("parse source %s: %w", path, err)
		}
		rel, err := filepath.Rel(sourceRoot, path)
		if err != nil {
			return fmt.Errorf("make source path relative for %s: %w", path, err)
		}
		for _, declaration := range file.Decls {
			function, ok := declaration.(*ast.FuncDecl)
			if !ok || function.Doc == nil {
				continue
			}
			cases, err := parseDoc(function.Doc.Text())
			if err != nil {
				return fmt.Errorf("parse markers on %s %s: %w", rel, function.Name.Name, err)
			}
			for _, item := range cases {
				item.Symbol = symbolOf(function)
				out = append(out, DiscoveredCase{Case: item, SourceFile: filepath.ToSlash(rel)})
			}
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	if err := checkCaseIDs(out); err != nil {
		return nil, err
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Case.ID < out[j].Case.ID })
	return out, nil
}

func symbolOf(function *ast.FuncDecl) string {
	if function.Recv == nil || len(function.Recv.List) == 0 {
		return function.Name.Name
	}
	expression := function.Recv.List[0].Type
	if pointer, ok := expression.(*ast.StarExpr); ok {
		expression = pointer.X
	}
	switch typed := expression.(type) {
	case *ast.IndexExpr:
		expression = typed.X
	case *ast.IndexListExpr:
		expression = typed.X
	}
	if identifier, ok := expression.(*ast.Ident); ok {
		return identifier.Name + "." + function.Name.Name
	}
	return function.Name.Name
}

func checkCaseIDs(cases []DiscoveredCase) error {
	seen := map[string]string{}
	for _, discovered := range cases {
		owner := discovered.SourceFile + "::" + discovered.Case.Symbol
		if previous, ok := seen[discovered.Case.ID]; ok {
			return fmt.Errorf(
				"case id collision %q: declared by both %s and %s",
				discovered.Case.ID, previous, owner,
			)
		}
		seen[discovered.Case.ID] = owner
	}
	return nil
}

func skipDir(name string) bool {
	switch name {
	case "vendor", "testdata", "node_modules":
		return true
	}
	return strings.HasPrefix(name, ".")
}
