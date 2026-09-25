import base64

import httpx

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
    ):
        self.api_key = api_key
        self.model = model
        self.name = f"{label}:{model}"
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")

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
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": build_system_prompt()},
                {"role": "user", "content": content},
            ],
        }
        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body,
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise VisionError(f"{self.name} request failed: {exc}") from exc
        try:
            text = resp.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise VisionError(f"{self.name} returned an unexpected response") from exc
        return parse_observations(text or "")
