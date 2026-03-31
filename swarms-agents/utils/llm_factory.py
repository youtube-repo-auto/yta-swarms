"""
LLM Factory – returns a thin LLMClient wrapper for the given model name.

Supported friendly names:
    "claude-3-5-sonnet"  → Anthropic SDK  (maps to claude-sonnet-4-6)
    "claude-haiku"       → Anthropic SDK  (maps to claude-haiku-4-5-20251001)
    "local"              → Local model via OpenAI-compatible API (Jarvis/Ollama)

Routing modes (ROUTING_MODE env var):
    "smart"        → auto-select local vs cloud based on max_tokens threshold
    "always_cloud" → always use Anthropic API (default)
    "always_local" → always use local model via OPENAI_BASE_URL
"""

import os
import logging
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

SUPPORTED_MODELS = {
    "claude-3-5-sonnet": "anthropic",
    "claude-haiku": "anthropic",
    "local": "local",
}

# Map friendly names → exact API model IDs.
_ANTHROPIC_IDS = {
    "claude-3-5-sonnet": "claude-sonnet-4-6",
    "claude-haiku": "claude-haiku-4-5-20251001",
}

# Threshold for smart routing: requests with max_tokens <= this use local model
_SMART_ROUTING_THRESHOLD = 2000


class LLMClient:
    """
    Thin wrapper that provides a uniform .run(task, system) → str interface
    over the native Anthropic Python SDK or a local OpenAI-compatible API.
    """

    def __init__(
        self,
        provider: str,
        model_id: str,
        api_key: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        base_url: str | None = None,
    ):
        self._provider = provider
        self._model_id = model_id
        self._api_key = api_key
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._base_url = base_url

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def run(self, task: str, system: str = "") -> str:
        """
        Call the LLM and return the full text response.
        """
        logger.debug(
            "LLMClient.run provider=%s model=%s max_tokens=%d",
            self._provider, self._model_id, self._max_tokens,
        )
        if self._provider == "local":
            return self._run_local(task, system)
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

    def _run_local(self, task: str, system: str) -> str:
        """Call a local OpenAI-compatible API (Jarvis/Ollama)."""
        import openai

        client = openai.OpenAI(
            api_key=self._api_key or "not-needed",
            base_url=self._base_url,
        )
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": task})

        response = client.chat.completions.create(
            model=self._model_id,
            messages=messages,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )
        return response.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# Factory function  (public API — unchanged signature)
# ---------------------------------------------------------------------------

def get_llm(model_name: str, max_tokens: int = 4096) -> LLMClient:
    """
    Factory function that returns the correct LLMClient based on model_name
    and the ROUTING_MODE environment variable.

    Routing modes:
        "always_cloud" (default) — always use Anthropic API
        "always_local"           — always use local model via OPENAI_BASE_URL
        "smart"                  — use local for max_tokens <= 2000, cloud otherwise
    """
    routing_mode = os.getenv("ROUTING_MODE", "always_cloud").lower()

    # Determine whether to use local or cloud
    use_local = False
    if routing_mode == "always_local":
        use_local = True
    elif routing_mode == "smart" and max_tokens <= _SMART_ROUTING_THRESHOLD:
        use_local = True
        logger.info(
            "Smart routing: using local model (max_tokens=%d <= %d)",
            max_tokens, _SMART_ROUTING_THRESHOLD,
        )

    if use_local:
        base_url = os.getenv("OPENAI_BASE_URL", "http://localhost:8000/v1")
        local_model = os.getenv("LOCAL_MODEL_NAME", "llama-3.1-8b")
        return LLMClient(
            provider="local",
            model_id=local_model,
            api_key=os.getenv("OPENAI_API_KEY", "not-needed"),
            max_tokens=max_tokens,
            temperature=0.7,
            base_url=base_url,
        )

    # Cloud path (Anthropic)
    if model_name not in SUPPORTED_MODELS or model_name == "local":
        # If explicitly requesting local but routing says cloud, fall through to haiku
        if model_name == "local":
            model_name = "claude-haiku"
        else:
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
