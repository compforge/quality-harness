// Package host observes runner or target host facts without interpreting cases.
package host

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"runtime"
	"strings"
	"time"

	"github.com/compforge/quality-harness/sdks/go/common"
)

// ObserveLocal observes this process's host. Remote fixtures must run it on
// the target or collect equivalent target evidence through their access adapter.
func ObserveLocal(ctx context.Context) (common.EnvironmentFacts, error) {
	facts := common.EnvironmentFacts{Source: "local:uname,os-release", ObservedAt: time.Now().UTC().Format(time.RFC3339Nano), Values: map[string]string{"os": runtime.GOOS, "arch": runtime.GOARCH}}
	out, err := exec.CommandContext(ctx, "uname", "-r").Output()
	if err != nil {
		return facts, fmt.Errorf("observe kernel release: %w", err)
	}
	facts.Values["kernel_version"] = strings.TrimSpace(string(out))
	if runtime.GOOS == "linux" {
		data, err := os.ReadFile("/etc/os-release")
		if err != nil {
			return facts, fmt.Errorf("observe distribution: %w", err)
		}
		for _, line := range strings.Split(string(data), "\n") {
			key, value, _ := strings.Cut(line, "=")
			if key == "ID" {
				facts.Values["distribution"] = strings.Trim(value, "\"")
			}
			if key == "VERSION_ID" {
				facts.Values["distribution_version"] = strings.Trim(value, "\"")
			}
		}
	}
	return facts, nil
}
