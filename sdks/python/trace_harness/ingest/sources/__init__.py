"""sources —— 采集协议：把各种遥测存储/形态归一成 NormSpan 集，喂给 assemble。

- `Source` / `SpanQuery`：后端无关的选择与取数协议（base）。
- `JaegerFileSource` / `load_jaeger_file`：离线文件。
- `OpenSearchSource`：在线 Jaeger-on-OpenSearch（通用参数版，env 发现留消费方）。

`select` 选择唯一 trace IDs，`fetch` 读取完整投影骨架，`read` 按引用补充证据。
"""

from __future__ import annotations

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
