"""sources —— 采集协议：把各种遥测存储/形态归一成 NormSpan 集，喂给 assemble。

- `Source` / `SpanQuery` / `Fidelity`：后端无关的选择与取数协议（base）。
- `JaegerFileSource` / `load_jaeger_file`：离线文件。
- `OpenSearchSource`：在线 Jaeger-on-OpenSearch（通用参数版，env 发现留消费方）。

`select`（按条件查命中 span，cohort 发现）与 `fetch`（取单 trace 全量，看调用栈）是两个
入口，下游 Model/Analyze/Render 共享——single-trace = cohort 的 N=1。
"""

from __future__ import annotations

from trace_harness.ingest.sources.base import (
    DEFAULT_DROP_ATTRS as DEFAULT_DROP_ATTRS,
)
from trace_harness.ingest.sources.base import (
    Fidelity as Fidelity,
)
from trace_harness.ingest.sources.base import (
    Source as Source,
)
from trace_harness.ingest.sources.base import (
    SpanQuery as SpanQuery,
)
from trace_harness.ingest.sources.jaeger_file import (
    JaegerFileSource as JaegerFileSource,
)
from trace_harness.ingest.sources.jaeger_file import (
    load_jaeger_file as load_jaeger_file,
)
from trace_harness.ingest.sources.jaeger_file import (
    normalize_es_doc as normalize_es_doc,
)
from trace_harness.ingest.sources.opensearch import (
    OpenSearchSource as OpenSearchSource,
)
