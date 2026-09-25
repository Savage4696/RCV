import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

from pydantic import ValidationError

from ..llm import ChatResult
from ..models import CatalogItem, Observations, POLine


class VisionError(RuntimeError):
    pass


@dataclass
class ImagePayload:
    photo_id: str
    content_type: str
    data: bytes


class VisionProvider(ABC):
    name: str
    last_call: ChatResult | None = None

    @abstractmethod
    def observe(
        self, line: POLine, catalog_item: CatalogItem | None, images: list[ImagePayload]
    ) -> Observations: ...


def parse_observations(text: str) -> Observations:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise VisionError("Vision model did not return JSON")
    try:
        return Observations.model_validate(json.loads(match.group(0)))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise VisionError(f"Vision model returned invalid observations: {exc}") from exc
