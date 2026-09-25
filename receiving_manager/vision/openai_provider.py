import base64

from ..llm import ChatResult, CreditBudget, LLMError, ResponseCache, chat_completion
from ..models import CatalogItem, Observations, POLine
from .base import ImagePayload, VisionError, VisionProvider, parse_observations
from .prompt import build_system_prompt, build_user_prompt


class OpenAIProvider(VisionProvider):
    """OpenAI chat-completions API, also used for OpenAI-compatible gateways (OpenRouter)."""

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float = 120,
        base_url: str = "https://api.openai.com/v1",
        label: str = "openai",
        budget: CreditBudget | None = None,
        cache: ResponseCache | None = None,
        max_tokens: int = 2500,
    ):
        self.api_key = api_key
        self.model = model
        self.name = f"{label}:{model}"
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        self.budget = budget
        self.cache = cache
        self.max_tokens = max_tokens
        self.last_call: ChatResult | None = None

    def observe(
        self, line: POLine, catalog_item: CatalogItem | None, images: list[ImagePayload]
    ) -> Observations:
        content: list[dict] = [
            {
                "type": "text",
                "text": build_user_prompt(line, catalog_item, [i.photo_id for i in images]),
            }
        ]
        for img in images:
            content.append({"type": "text", "text": f"photo_id: {img.photo_id}"})
            b64 = base64.b64encode(img.data).decode()
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{img.content_type};base64,{b64}", "detail": "high"},
                }
            )
        body = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": build_system_prompt()},
                {"role": "user", "content": content},
            ],
        }
        try:
            self.last_call = chat_completion(
                self.base_url,
                self.api_key,
                body,
                timeout=self.timeout,
                budget=self.budget,
                cache=self.cache,
                estimate_usd=0.01 + 0.006 * len(images),
            )
        except LLMError as exc:
            raise VisionError(f"{self.name}: {exc}") from exc
        return parse_observations(self.last_call.text)
