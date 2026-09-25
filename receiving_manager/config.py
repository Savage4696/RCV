import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    vision_provider: str
    openai_api_key: str | None
    openai_model: str
    openrouter_api_key: str | None
    openrouter_model: str
    anthropic_api_key: str | None
    anthropic_model: str
    confidence_threshold: float
    data_dir: Path
    reasoning_model: str
    reasoning_effort: str
    reasoning_enabled: bool
    min_credit_usd: float
    max_spend_usd: float


def load_settings() -> Settings:
    openai_key = os.environ.get("OPENAI_API_KEY") or None
    openrouter_key = os.environ.get("OPENROUTER_API_KEY") or None
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY") or None
    default_provider = (
        "openai"
        if openai_key
        else "openrouter"
        if openrouter_key
        else "anthropic"
        if anthropic_key
        else "none"
    )
    return Settings(
        vision_provider=os.environ.get("RM_VISION_PROVIDER", default_provider).lower(),
        openai_api_key=openai_key,
        openai_model=os.environ.get("RM_OPENAI_MODEL", "gpt-4o"),
        openrouter_api_key=openrouter_key,
        openrouter_model=os.environ.get("RM_OPENROUTER_MODEL", "openai/gpt-4o"),
        anthropic_api_key=anthropic_key,
        anthropic_model=os.environ.get("RM_ANTHROPIC_MODEL", "claude-3-5-sonnet-latest"),
        confidence_threshold=float(os.environ.get("RM_CONFIDENCE_THRESHOLD", "0.7")),
        data_dir=Path(os.environ.get("RM_DATA_DIR", "data")),
        reasoning_model=os.environ.get("RM_REASONING_MODEL", "openai/gpt-5-mini"),
        reasoning_effort=os.environ.get("RM_REASONING_EFFORT", "medium"),
        reasoning_enabled=os.environ.get("RM_REASONING", "on").lower() not in ("0", "off", "false"),
        min_credit_usd=float(os.environ.get("RM_MIN_CREDIT_USD", "0.25")),
        max_spend_usd=float(os.environ.get("RM_MAX_SPEND_USD", "1.00")),
    )
