// Command casegen checks that every source-level +case marker has one static
// caserun.Ref to the selected canonical CaseSet.
package main

import (
	"flag"
	"fmt"
	"os"
	"text/tabwriter"

	"github.com/compforge/quality-harness/sdks/go/e2e/contract"
)

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}
	var code int
	switch os.Args[1] {
	case "list":
		code = cmdList(os.Args[2:])
	case "check":
		code = cmdCheck(os.Args[2:])
	case "-h", "--help", "help":
		usage()
		return
	default:
		fmt.Fprintf(os.Stderr, "casegen: unknown subcommand %q\n\n", os.Args[1])
		usage()
		code = 2
	}
	os.Exit(code)
}

func usage() {
	fmt.Fprint(os.Stderr, `casegen — check +case marker to CaseRun coverage

usage:
  casegen list  --source DIR
  casegen check --source DIR --test DIR --caseset NAME

  list   print every +case marker under --source
  check  require exactly one literal caserun.Ref(NAME, case_id) per marker
`)
}

func cmdList(args []string) int {
	flags := flag.NewFlagSet("casegen list", flag.ContinueOnError)
	source := flags.String("source", "", "dir to scan for +case markers")
	if err := flags.Parse(args); err != nil {
		return 2
	}
	if *source == "" {
		fmt.Fprintln(os.Stderr, "casegen list: --source is required")
		return 2
	}
	cases, err := contract.Discover(*source)
	if err != nil {
		fmt.Fprintf(os.Stderr, "casegen: discover: %v\n", err)
		return 1
	}
	w := tabwriter.NewWriter(os.Stdout, 0, 4, 2, ' ', 0)
	fmt.Fprintln(w, "CASE\tSPEC\tSYMBOL\tSOURCE")
	for _, discovered := range cases {
		fmt.Fprintf(w, "%s\t%s\t%s\t%s\n", discovered.Case.ID, discovered.Case.SpecID, discovered.Case.Symbol, discovered.SourceFile)
	}
	_ = w.Flush()
	fmt.Printf("\n%d case(s)\n", len(cases))
	return 0
}

func cmdCheck(args []string) int {
	flags := flag.NewFlagSet("casegen check", flag.ContinueOnError)
	source := flags.String("source", "", "dir to scan for +case markers")
	test := flags.String("test", "", "dir to scan for caserun.Ref calls")
	caseset := flags.String("caseset", "", "canonical CaseSet name used by caserun.Ref")
	if err := flags.Parse(args); err != nil {
		return 2
	}
	if *source == "" || *test == "" || *caseset == "" {
		fmt.Fprintln(os.Stderr, "casegen check: --source, --test, and --caseset are required")
		return 2
	}
	cases, err := contract.Discover(*source)
	if err != nil {
		fmt.Fprintf(os.Stderr, "casegen: discover markers: %v\n", err)
		return 1
	}
	refs, err := contract.DiscoverRefs(*test)
	if err != nil {
		fmt.Fprintf(os.Stderr, "casegen: discover refs: %v\n", err)
		return 1
	}
	coverage := contract.CheckCoverage(*caseset, cases, refs)
	for _, id := range coverage.Missing {
		fmt.Printf("missing    %s\n", id)
	}
	for _, id := range coverage.Orphaned {
		fmt.Printf("orphaned   %s\n", id)
	}
	for _, id := range coverage.Duplicate {
		fmt.Printf("duplicate  %s\n", id)
	}
	if !coverage.OK() {
		fmt.Fprintln(os.Stderr, "casegen: marker/CaseRun coverage is incomplete")
		return 1
	}
	fmt.Printf("all %d case(s) have exactly one %s CaseRun\n", len(cases), *caseset)
	return 0
}
