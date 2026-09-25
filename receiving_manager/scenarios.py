"""Loader for the bundled test scenarios (scenarios/*.json).

Scenario photos are placeholders identified by photo_id; the observations stand in for what a
vision model or human inspector recorded for those photos.
"""

import json
from pathlib import Path

from pydantic import BaseModel, Field

from .evidence import sha256_bytes
from .models import CatalogItem, Observations, PhotoInput, PurchaseOrder

SCENARIO_DIR = Path(__file__).resolve().parent.parent / "scenarios"


class ScenarioExpectation(BaseModel):
    decision: str
    checks: dict[str, str] = Field(default_factory=dict)
    issue_codes: list[str] = Field(default_factory=list)


class Scenario(BaseModel):
    name: str
    title: str
    description: str
    purchase_order: PurchaseOrder
    photos: list[str]
    observations: Observations
    expected: ScenarioExpectation


def load_catalog(directory: Path = SCENARIO_DIR) -> list[CatalogItem]:
    data = json.loads((directory / "catalog.json").read_text())
    return [CatalogItem.model_validate(c) for c in data]


def load_scenarios(directory: Path = SCENARIO_DIR) -> list[Scenario]:
    return [
        Scenario.model_validate({"name": p.stem, **json.loads(p.read_text())})
        for p in sorted(directory.glob("*.json"))
        if p.name != "catalog.json"
    ]


def placeholder_photos(scenario: Scenario) -> list[PhotoInput]:
    photos = []
    for pid in scenario.photos:
        data = f"scenario:{scenario.name}:{pid}".encode()
        photos.append(
            PhotoInput(
                photo_id=pid, filename=f"{pid}.jpg", sha256=sha256_bytes(data), size_bytes=len(data)
            )
        )
    return photos
