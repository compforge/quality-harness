//go:build e2e

package sandbox_e2e

import (
	"os"
	"testing"

	"github.com/compforge/quality-harness/sdks/go/e2e/testrun"
)

var sandboxRun = testrun.New("sandbox-server")

func TestMain(m *testing.M) {
	os.Exit(sandboxRun.Main(m.Run))
}
