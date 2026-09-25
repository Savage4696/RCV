import base64

import httpx

from ..models import CatalogItem, Observations, POLine
from .base import ImagePayload, VisionError, VisionProvider, parse_observations
from .prompt import build_system_prompt, build_user_prompt


class OpenAIProvider(VisionProvider):
    def __init__(self, api_key: str, model: str, timeout: float = 120):
        self.api_key = api_key
        self.model = model
        self.name = f"openai:{model}"
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
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body,
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise VisionError(f"OpenAI request failed: {exc}") from exc
        return parse_observations(resp.json()["choices"][0]["message"]["content"])
