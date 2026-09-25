import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    vision_provider: str
    openai_api_key: str | None
    openai_model: str
    anthropic_api_key: str | None
    anthropic_model: str
    confidence_threshold: float
    data_dir: Path


def load_settings() -> Settings:
    openai_key = os.environ.get("OPENAI_API_KEY") or None
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY") or None
    default_provider = "openai" if openai_key else "anthropic" if anthropic_key else "none"
    return Settings(
        vision_provider=os.environ.get("RM_VISION_PROVIDER", default_provider).lower(),
        openai_api_key=openai_key,
        openai_model=os.environ.get("RM_OPENAI_MODEL", "gpt-4o"),
        anthropic_api_key=anthropic_key,
        anthropic_model=os.environ.get("RM_ANTHROPIC_MODEL", "claude-3-5-sonnet-latest"),
        confidence_threshold=float(os.environ.get("RM_CONFIDENCE_THRESHOLD", "0.7")),
        data_dir=Path(os.environ.get("RM_DATA_DIR", "data")),
    )
