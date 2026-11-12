"""Connection paths preserve logical identity; protocol clients own the protocol."""

from harness_toolbox.transport.base import DirectTransport as DirectTransport
from harness_toolbox.transport.base import Endpoint as Endpoint
from harness_toolbox.transport.base import Transport as Transport
from harness_toolbox.transport.connection import ConnectionSource as ConnectionSource
from harness_toolbox.transport.kubernetes import KubernetesAccess as KubernetesAccess
from harness_toolbox.transport.pod_python import PodPythonTransport as PodPythonTransport
from harness_toolbox.transport.pod_relay import PodRelayClient as PodRelayClient
from harness_toolbox.transport.pod_relay import PodRelayTransport as PodRelayTransport
from harness_toolbox.transport.port_forward import (
    KubernetesPortForwardTransport as KubernetesPortForwardTransport,
)
from harness_toolbox.transport.port_forward import PortForwardTransport as PortForwardTransport
from harness_toolbox.transport.socks import SocksProxy as SocksProxy
