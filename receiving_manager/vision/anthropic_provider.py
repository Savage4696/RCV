import base64

import httpx

from ..models import CatalogItem, Observations, POLine
from .base import ImagePayload, VisionError, VisionProvider, parse_observations
from .prompt import build_system_prompt, build_user_prompt


class AnthropicProvider(VisionProvider):
    def __init__(self, api_key: str, model: str, timeout: float = 120):
        self.api_key = api_key
        self.model = model
        self.name = f"anthropic:{model}"
        self.timeout = timeout

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
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": img.content_type,
                        "data": base64.b64encode(img.data).decode(),
                    },
                }
            )
        body = {
            "model": self.model,
            "max_tokens": 4096,
            "temperature": 0,
            "system": build_system_prompt(),
            "messages": [{"role": "user", "content": content}],
        }
        try:
            resp = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                },
                json=body,
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise VisionError(f"Anthropic request failed: {exc}") from exc
        text = "".join(b.get("text", "") for b in resp.json()["content"] if b["type"] == "text")
        return parse_observations(text)
