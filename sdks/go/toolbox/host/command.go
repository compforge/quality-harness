package host

import (
	"context"
	"fmt"
	"os/exec"
	"strings"

	"github.com/compforge/quality-harness/sdks/go/common"
)

// Command creates a command on the optional environment Host. The caller owns
// the deadline, I/O limits and any remote resources created by the command.
// SSH re-serializes argv through a remote shell, so every argument is quoted.
func Command(ctx context.Context, host *common.Host, argv ...string) (*exec.Cmd, error) {
	if len(argv) == 0 || argv[0] == "" {
		return nil, fmt.Errorf("host command requires a program")
	}
	if host != nil {
		if err := host.Validate(); err != nil {
			return nil, err
		}
		if host.ResolvedTransport() == "ssh" {
			quoted := make([]string, len(argv))
			for i, arg := range argv {
				quoted[i] = "'" + strings.ReplaceAll(arg, "'", "'\"'\"'") + "'"
			}
			return exec.CommandContext(ctx, "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host.Address, strings.Join(quoted, " ")), nil
		}
	}
	return exec.CommandContext(ctx, argv[0], argv[1:]...), nil
}
