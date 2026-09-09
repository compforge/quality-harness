"""Profile and capability gating for e2e tests.

Tests that depend on specific server-side configuration (warm pool, quota,
storage class, etc.) call these gates at the top of the test body. Mismatched
profile -> skip rather than fail.
"""

from __future__ import annotations

import pytest

from e2e_harness.core.config import E2EConfig


def require_profile(config: E2EConfig, *profiles: str) -> None:
    """Skip the current test if config.profile is not in the allowed set."""
    if config.profile not in profiles:
        pytest.skip(
            f"profile={config.profile!r} not in {profiles}; "
            f"set E2E_PROFILE={profiles[0]} to run"
        )


def require_capability(config: E2EConfig, key: str) -> None:
    """Skip if the server did not report the named capability."""
    if not config.capabilities.known:
        pytest.skip(
            f"capabilities unknown (server /healthz not reachable); cannot confirm {key!r}"
        )
    if not config.capabilities.data.get(key):
        pytest.skip(f"capability {key!r} not available on this server")
