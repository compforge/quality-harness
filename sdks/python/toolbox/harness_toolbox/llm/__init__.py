"""OpenAI-compatible model access: requests, credentials, timeout and retries.

This protocol implementation belongs to toolbox; common remains standard-library-only.
Domain SDKs compose the client and own prompts and evaluation policy.
"""

from harness_toolbox.llm.client import ChatResult as ChatResult
from harness_toolbox.llm.client import LLMClient as LLMClient
from harness_toolbox.llm.client import LLMConfig as LLMConfig
