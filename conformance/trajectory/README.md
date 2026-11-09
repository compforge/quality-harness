# Session measurement conformance

`session-measurements.json` records shared agent-ledger SessionView facts. The Go
trajectory SDK consumes them directly; the Python test maps those same facts to
ATIF using its existing construction helpers. Both verify the explicit expected
Session and Global values, including missing usage, zero durations, partial
coverage, cache-write inclusion, failed tools and empty Sessions.

Common summary operations use sum, mean and nearest-rank p50/p95. Failure ratios
are explicit failures divided by all observed tool calls; known-status coverage
is a separate Go metric. Runtime-specific evidence source IDs and additional
measurements may differ; no quality verdict is implied.
