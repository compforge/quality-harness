package host

import (
	"context"
	"os/exec"
	"strings"
	"testing"

	"github.com/compforge/quality-harness/sdks/go/common"
)

func TestRemoteArgumentsRemainLiteral(t *testing.T) {
	argv := []string{"printf", "%s", "file with ' quote; $(exit 7)\nnext"}
	cmd, err := Command(context.Background(), &common.Host{Name: "devbox", Transport: "ssh", Address: "builder"}, argv...)
	if err != nil {
		t.Fatal(err)
	}
	// Execute the remote shell payload locally; shell syntax in an argument
	// must survive unchanged, including quotes and embedded newlines.
	out, err := exec.Command("sh", "-c", cmd.Args[len(cmd.Args)-1]).Output()
	if err != nil || string(out) != argv[2] {
		t.Fatalf("out=%q err=%v", out, err)
	}
}

func TestLocalCommandAndFacts(t *testing.T) {
	cmd, err := Command(context.Background(), nil, "printf", "%s", "local")
	if err != nil {
		t.Fatal(err)
	}
	out, err := cmd.Output()
	if err != nil || string(out) != "local" {
		t.Fatalf("out=%q err=%v", out, err)
	}
	facts, err := ObserveLocal(context.Background())
	if err != nil || facts.Values["os"] == "" || strings.TrimSpace(facts.Values["kernel_version"]) == "" {
		t.Fatalf("facts=%+v err=%v", facts, err)
	}
}
