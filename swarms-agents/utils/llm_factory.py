"""
LLM Factory - returns the correct LLM object based on model name.
Supports: claude-3-5-sonnet, gpt-4-turbo, gpt-4o-mini
"""

import os
from dotenv import load_dotenv

load_dotenv()

SUPPORTED_MODELS = {
    "claude-3-5-sonnet": "anthropic",
    "gpt-4-turbo": "openai",
    "gpt-4o-mini": "openai",
}


def get_llm(model_name: str):
    """
    Factory function that returns the correct LLM client based on model_name.

    Args:
        model_name: One of 'claude-3-5-sonnet', 'gpt-4-turbo', 'gpt-4o-mini'

    Returns:
        Configured LLM object compatible with the swarms framework.

    Raises:
        ValueError: If model_name is not supported.
        EnvironmentError: If the required API key is missing.
    """
    if model_name not in SUPPORTED_MODELS:
        raise ValueError(
            f"Unsupported model '{model_name}'. "
            f"Choose from: {list(SUPPORTED_MODELS.keys())}"
        )

    provider = SUPPORTED_MODELS[model_name]

    if provider == "anthropic":
        return _build_anthropic_llm(model_name)
    elif provider == "openai":
        return _build_openai_llm(model_name)

    raise ValueError(f"Unknown provider '{provider}' for model '{model_name}'")


def _build_anthropic_llm(model_name: str):
    """Build an Anthropic LLM compatible with swarms."""
    from swarms import Claude

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY is not set in environment variables."
        )

    # Map friendly name → exact Anthropic model ID
    model_id_map = {
        "claude-3-5-sonnet": "claude-3-5-sonnet-20241022",
    }
    model_id = model_id_map.get(model_name, model_name)

    return Claude(
        api_key=api_key,
        model=model_id,
        max_tokens=4096,
        temperature=0.7,
    )


def _build_openai_llm(model_name: str):
    """Build an OpenAI LLM compatible with swarms."""
    from swarms import OpenAIChat

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "OPENAI_API_KEY is not set in environment variables."
        )

    # Map friendly name → exact OpenAI model ID
    model_id_map = {
        "gpt-4-turbo": "gpt-4-turbo-preview",
        "gpt-4o-mini": "gpt-4o-mini",
    }
    model_id = model_id_map.get(model_name, model_name)

    return OpenAIChat(
        openai_api_key=api_key,
        model_name=model_id,
        max_tokens=4096,
        temperature=0.7,
    )
