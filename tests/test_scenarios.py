import pytest

from receiving_manager.engine import inspect
from receiving_manager.scenarios import load_catalog, load_scenarios, placeholder_photos

CATALOG = load_catalog()
SCENARIOS = load_scenarios()


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_scenario(scenario):
    report, _ = inspect(
        scenario.purchase_order, CATALOG, placeholder_photos(scenario), scenario.observations
    )
    verdicts = {c.check: c.verdict.value for c in report.checks}
    assert report.decision.value == scenario.expected.decision, report.decision_reason
    for check, verdict in scenario.expected.checks.items():
        assert verdicts[check] == verdict, f"{check}: {verdicts[check]} != {verdict}"
    codes = {i.code for i in report.issues}
    assert set(scenario.expected.issue_codes) <= codes


def test_all_required_scenarios_present():
    names = {s.name.split("_", 1)[1] for s in SCENARIOS}
    for required in [
        "correct_shipment",
        "short_shipment",
        "extra_units",
        "wrong_sku",
        "wrong_variant",
        "crushed_carton",
        "water_damaged_carton",
        "torn_packaging",
        "missing_components",
        "ambiguous",
    ]:
        assert required in names
