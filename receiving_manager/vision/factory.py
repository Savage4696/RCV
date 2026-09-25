from ..config import Settings
from ..llm import CreditBudget, ResponseCache
from ..reasoning import ReasoningReviewer
from .anthropic_provider import AnthropicProvider
from .base import VisionProvider
from .openai_provider import OpenAIProvider

OPENROUTER_URL = "https://openrouter.ai/api/v1"


def make_budget(settings: Settings) -> CreditBudget:
    return CreditBudget(
        api_key=settings.openrouter_api_key,
        base_url=OPENROUTER_URL,
        min_remaining_usd=settings.min_credit_usd,
        max_spend_usd=settings.max_spend_usd,
    )


def make_cache(settings: Settings) -> ResponseCache:
    return ResponseCache(settings.data_dir / "llm-cache")


def get_provider(
    settings: Settings, budget: CreditBudget | None = None, cache: ResponseCache | None = None
) -> VisionProvider | None:
    if settings.vision_provider == "openai" and settings.openai_api_key:
        return OpenAIProvider(
            settings.openai_api_key, settings.openai_model, budget=budget, cache=cache
        )
    if settings.vision_provider == "openrouter" and settings.openrouter_api_key:
        return OpenAIProvider(
            settings.openrouter_api_key,
            settings.openrouter_model,
            base_url=OPENROUTER_URL,
            label="openrouter",
            budget=budget,
            cache=cache,
        )
    if settings.vision_provider == "anthropic" and settings.anthropic_api_key:
        return AnthropicProvider(settings.anthropic_api_key, settings.anthropic_model)
    return None


def get_reviewer(
    settings: Settings, budget: CreditBudget | None = None, cache: ResponseCache | None = None
) -> ReasoningReviewer | None:
    if not (settings.reasoning_enabled and settings.openrouter_api_key):
        return None
    return ReasoningReviewer(
        settings.openrouter_api_key,
        settings.reasoning_model,
        base_url=OPENROUTER_URL,
        effort=settings.reasoning_effort,
        budget=budget,
        cache=cache,
    )
