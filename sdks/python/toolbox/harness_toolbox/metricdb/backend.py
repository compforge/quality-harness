"""Select an explicit supported backend without guessing from an endpoint URL."""

from .model import MetricDBBackend
from .prometheus import PrometheusBackend
from .victoriametrics import VictoriaMetricsBackend


def backend_for(kind: str) -> MetricDBBackend:
    if kind == "prometheus":
        return PrometheusBackend()
    if kind == "victoriametrics":
        return VictoriaMetricsBackend()
    raise ValueError("Unsupported MetricDB backend kind")
