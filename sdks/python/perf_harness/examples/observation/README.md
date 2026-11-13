# Project observation assets

`product.graph.json` describes topology and Operation metadata. `observations.yaml` declares
HTTP RED measurements. `experiment.yaml` binds the environment, data source, Case and load.
Adjust URLs, exporter names/labels and the request adapter to your project before running.
The example performs real requests and queries existing Prometheus history; it does not scrape /metrics.
HTTP status errors are not SSE business errors. See [observation declarations](../../docs/observation.md).
