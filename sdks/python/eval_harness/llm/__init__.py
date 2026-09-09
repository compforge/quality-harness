"""LLM access layer — re-export of ``common.llm``.

The client moved to ``common.llm`` (harness-neutral shared infra). This thin re-export keeps
``eval_harness.llm`` importable for existing consumers (eval-suite); new code should import from
``common.llm`` directly.
"""

from harness_common.llm import ChatResult as ChatResult
from harness_common.llm import LLMClient as LLMClient
from harness_common.llm import LLMConfig as LLMConfig
