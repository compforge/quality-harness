"""Compatibility import; Kubernetes tunnels live in harness_toolbox.transport."""

from harness_toolbox.transport.port_forward import (
    KubernetesPortForwardTransport as KubernetesPortForwardTransport,
)
