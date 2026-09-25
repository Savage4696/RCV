"""Loader for the bundled test scenarios (scenarios/*.json).

Scenario photos are placeholders identified by photo_id; the observations stand in for what a
vision model or human inspector recorded for those photos.
"""

import json
from pathlib import Path

from pydantic import BaseModel, Field

from .engine import inspect
from .evidence import sha256_bytes
from .models import CatalogItem, InspectionReport, Observations, PhotoInput, PurchaseOrder

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


def evaluate(
    scenario: Scenario, catalog: list[CatalogItem], threshold: float = 0.7
) -> tuple[InspectionReport, list[str]]:
    """Run a scenario through the rules; return the report and any mismatches vs. expected."""
    report, _ = inspect(
        scenario.purchase_order,
        catalog,
        placeholder_photos(scenario),
        scenario.observations,
        threshold=threshold,
    )
    verdicts = {c.check: c.verdict.value for c in report.checks}
    codes = {i.code for i in report.issues}
    mismatches = []
    if report.decision.value != scenario.expected.decision:
        mismatches.append(f"decision {report.decision.value} != {scenario.expected.decision}")
    mismatches += [
        f"{check} {verdicts.get(check)} != {verdict}"
        for check, verdict in scenario.expected.checks.items()
        if verdicts.get(check) != verdict
    ]
    mismatches += [f"missing issue {c}" for c in scenario.expected.issue_codes if c not in codes]
    return report, mismatches
