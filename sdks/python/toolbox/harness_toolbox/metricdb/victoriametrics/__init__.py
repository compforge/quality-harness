"""VictoriaMetrics query behavior behind the MetricDB boundary."""


class VictoriaMetricsBackend:
    """Keep frozen times and grids independently of sample arrival.

    @spec VM must not silently shift the requested evaluation time.
    @why nocache prevents instant shifts and grid alignment; range tail adjustment
         also requires latency_offset. Older VM versions accept a minimum of 1ms.
    @link https://github.com/VictoriaMetrics/VictoriaMetrics/issues/241
    """

    def query_parameters(self) -> dict[str, str]:
        return {"nocache": "1", "latency_offset": "1ms"}
