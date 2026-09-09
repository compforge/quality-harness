"""common.llm — OpenAI-compatible LLM client, harness-neutral shared infrastructure.

A model call (endpoint / auth / timeout / retry / provenance) is not specific to any one
harness: eval's LLM-as-judge metrics use it today, and e2e's casegen ``LLMCompiler`` will use
it tomorrow. So it lives in ``common`` (like ``case`` / ``verdict`` / ``facets``) — the 3 SDKs
compose ``LLMClient`` without importing each other.
"""

from harness_common.llm.client import ChatResult as ChatResult
from harness_common.llm.client import LLMClient as LLMClient
from harness_common.llm.client import LLMConfig as LLMConfig
