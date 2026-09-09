package core

import "testing"

// RequireProfile skips the test if config.Profile is not in the allowed set.
func RequireProfile(t *testing.T, config *E2EConfig, profiles ...string) {
	t.Helper()
	for _, p := range profiles {
		if config.Profile == p {
			return
		}
	}
	t.Skipf("profile=%q not in %v; skipping", config.Profile, profiles)
}

// RequireCapability skips the test if the server didn't report the named capability.
func RequireCapability(t *testing.T, config *E2EConfig, key string) {
	t.Helper()
	if !config.Capabilities.Known {
		t.Skipf("capabilities unknown; cannot confirm %q", key)
		return
	}
	if _, ok := config.Capabilities.Data[key]; !ok {
		t.Skipf("capability %q not available on this server", key)
	}
}
