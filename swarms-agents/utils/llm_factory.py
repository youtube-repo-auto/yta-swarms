"""
LLM Factory – returns a thin LLMClient wrapper for the given model name.

Supported friendly names:
    "claude-3-5-sonnet"  → Anthropic SDK  (maps to claude-sonnet-4-6)
    "claude-haiku"       → Anthropic SDK  (maps to claude-haiku-4-5-20251001)

No swarms dependency.
"""

import os
import logging
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

SUPPORTED_MODELS = {
    "claude-3-5-sonnet": "anthropic",
    "claude-haiku": "anthropic",
}

# Map friendly names → exact API model IDs.
# claude-3-5-sonnet-20241022 was retired Oct 2025; claude-sonnet-4-6 is the
# current equivalent and maintains the same quality for long-form Dutch content.
_ANTHROPIC_IDS = {
    "claude-3-5-sonnet": "claude-sonnet-4-6",
    "claude-haiku": "claude-haiku-4-5-20251001",
}


class LLMClient:
    """
    Thin wrapper that provides a uniform .run(task, system) → str interface
    over the native Anthropic Python SDK.

    Replaces the Swarms Agent class for all agents in this pipeline.
    """

    def __init__(
        self,
        provider: str,
        model_id: str,
        api_key: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ):
        self._provider = provider
        self._model_id = model_id
        self._api_key = api_key
        self._max_tokens = max_tokens
        self._temperature = temperature

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def run(self, task: str, system: str = "") -> str:
        """
        Call the LLM and return the full text response.

        Args:
            task:   The user message / task to complete.
            system: Optional system prompt.

        Returns:
            The model's text response as a plain string.
        """
        logger.debug(
            "LLMClient.run provider=%s model=%s max_tokens=%d",
            self._provider, self._model_id, self._max_tokens,
        )
        return self._run_anthropic(task, system)

    # ------------------------------------------------------------------
    # Provider implementations
    # ------------------------------------------------------------------

    def _run_anthropic(self, task: str, system: str) -> str:
        """Call the Anthropic Messages API with streaming (avoids timeouts)."""
        import anthropic

        client = anthropic.Anthropic(api_key=self._api_key)
        kwargs: dict = {
            "model": self._model_id,
            "max_tokens": self._max_tokens,
            "messages": [{"role": "user", "content": task}],
        }
        if system:
            kwargs["system"] = system

        # Stream by default: prevents HTTP timeouts for long outputs (scripts)
        with client.messages.stream(**kwargs) as stream:
            message = stream.get_final_message()

        text_blocks = [b.text for b in message.content if b.type == "text"]
        return "".join(text_blocks)

# ---------------------------------------------------------------------------
# Factory function  (public API — unchanged signature)
# ---------------------------------------------------------------------------

def get_llm(model_name: str, max_tokens: int = 4096) -> LLMClient:
    """
    Factory function that returns the correct LLMClient based on model_name.

    Args:
        model_name: One of 'claude-3-5-sonnet', 'claude-haiku'
        max_tokens: Maximum tokens the model may generate (default 4096).

    Returns:
        LLMClient instance with .run(task, system) → str interface.

    Raises:
        ValueError:         If model_name is not supported.
        EnvironmentError:   If the required API key is missing.
    """
    if model_name not in SUPPORTED_MODELS:
        raise ValueError(
            f"Unsupported model '{model_name}'. "
            f"Choose from: {list(SUPPORTED_MODELS.keys())}"
        )

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY is not set.")
    model_id = _ANTHROPIC_IDS.get(model_name, model_name)
    return LLMClient(
        provider="anthropic",
        model_id=model_id,
        api_key=api_key,
        max_tokens=max_tokens,
        temperature=0.7,
    )
