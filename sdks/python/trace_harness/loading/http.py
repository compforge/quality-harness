"""Evidence required to distinguish ordinary HTTP from streaming calls."""

from trace_harness.kinds.http import http_endpoint
from trace_harness.loading.facts import EvidenceDependency, FactProducer

HTTP_FIELDS = (
    "http.request.header.accept",
    "http.response.header.content-type",
    "http.response.header.content_type",
    "http.request.headers",
    "http.response.headers",
    "http.request.body",
    "http.request.body.json",
)


def dependencies(node, trace):
    endpoints = {sid for sid, span in trace.spans.items() if http_endpoint(span)}
    ids = tuple(
        sid
        for sid, span in trace.spans.items()
        if sid in endpoints
        or (span.parent_span_id in endpoints and span.name in {"request-body", "response-body"})
    )
    return (EvidenceDependency(ids, HTTP_FIELDS),) if ids else ()


# Dependency-only preparation is memoized by the run; it adds no business fact to the IR.
HTTP_EVIDENCE = FactProducer(("http_evidence",), lambda n: True, dependencies, lambda n, t: {})
