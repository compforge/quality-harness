# Example: canonical API case

The Service declares `create_widget` in `config.yaml` with its HTTP method and route. The Case in `cases.yaml` references that name through `input.operation` and supplies the request body and assertions. The engine validates all bindings before sending requests and writes `verdict.json`.

```bash
export WIDGET_TOKEN=...
e2e run cases.yaml --config config.yaml --runs-dir ./runs
```

For a case that needs resource setup, multiple operations, eventual convergence or expensive teardown, use CaseRun directly as shown in `../python-service` or `../go-service`. Lifecycle code remains outside the CaseSet.
