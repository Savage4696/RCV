from ..config import Settings
from .anthropic_provider import AnthropicProvider
from .base import VisionProvider
from .openai_provider import OpenAIProvider


def get_provider(settings: Settings) -> VisionProvider | None:
    if settings.vision_provider == "openai" and settings.openai_api_key:
        return OpenAIProvider(settings.openai_api_key, settings.openai_model)
    if settings.vision_provider == "openrouter" and settings.openrouter_api_key:
        return OpenAIProvider(
            settings.openrouter_api_key,
            settings.openrouter_model,
            base_url="https://openrouter.ai/api/v1",
            label="openrouter",
        )
    if settings.vision_provider == "anthropic" and settings.anthropic_api_key:
        return AnthropicProvider(settings.anthropic_api_key, settings.anthropic_model)
    return None
