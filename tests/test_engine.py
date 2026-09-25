from receiving_manager.engine import inspect
from receiving_manager.models import Decision, Observations, Verdict
from receiving_manager.scenarios import load_catalog, load_scenarios, placeholder_photos

CATALOG = load_catalog()
SCENARIOS = {s.name: s for s in load_scenarios()}


def run(name, mutate=None, threshold=0.7):
    s = SCENARIOS[name].model_copy(deep=True)
    if mutate:
        mutate(s.observations)
    report, _ = inspect(
        s.purchase_order, CATALOG, placeholder_photos(s), s.observations, threshold=threshold
    )
    return report, {c.check: c for c in report.checks}


def test_no_observations_is_uncertain_not_accept():
    s = SCENARIOS["01_correct_shipment"]
    report, _ = inspect(s.purchase_order, CATALOG, placeholder_photos(s), Observations())
    assert report.decision == Decision.UNCERTAIN


def test_no_photos_is_uncertain_even_with_observations():
    s = SCENARIOS["01_correct_shipment"]
    report, _ = inspect(s.purchase_order, CATALOG, [], s.observations)
    assert report.decision == Decision.UNCERTAIN
    assert any("No usable photos" in w for w in report.warnings)


def test_low_confidence_count_is_uncertain():
    def m(o):
        o.units.count = 22
        o.units.confidence = 0.5

    _, checks = run("01_correct_shipment", m)
    assert checks["quantity"].verdict == Verdict.UNCERTAIN


def test_threshold_is_configurable():
    def m(o):
        o.units.confidence = 0.75

    _, checks = run("01_correct_shipment", m, threshold=0.8)
    assert checks["quantity"].verdict == Verdict.UNCERTAIN


def test_partial_count_above_expected_is_over_shipment():
    def m(o):
        o.units.count = 30
        o.units.all_visible = False

    _, checks = run("01_correct_shipment", m)
    assert checks["quantity"].verdict == Verdict.FAIL
    assert checks["quantity"].issues[0].code == "OVER_SHIPMENT"


def test_partial_count_below_expected_is_uncertain_not_short():
    def m(o):
        o.units.count = 20
        o.units.all_visible = False

    _, checks = run("01_correct_shipment", m)
    assert checks["quantity"].verdict == Verdict.UNCERTAIN


def test_sealed_cartons_derive_quantity_with_reduced_confidence():
    _, checks = run("06_crushed_carton")
    q = checks["quantity"]
    assert q.verdict == Verdict.PASS and q.observed == 24
    assert q.confidence < 0.9 and "not visually verified" in q.reason


def test_mixed_sku_detected():
    def m(o):
        o.identifiers.append(o.identifiers[0].model_copy(update={"value": "RED-BOTTLE-001"}))

    _, checks = run("01_correct_shipment", m)
    assert checks["sku_identity"].verdict == Verdict.FAIL
    assert checks["sku_identity"].issues[0].code == "MIXED_SKU"


def test_unrecognised_barcode_only_is_uncertain():
    def m(o):
        o.identifiers = [o.identifiers[1].model_copy(update={"value": "999999999999"})]

    _, checks = run("01_correct_shipment", m)
    assert checks["sku_identity"].verdict == Verdict.UNCERTAIN


def test_product_name_alone_does_not_identify_sku():
    def m(o):
        o.identifiers = [
            o.identifiers[0].model_copy(
                update={"kind": "product_name", "value": "Insulated Water Bottle"}
            )
        ]

    _, checks = run("01_correct_shipment", m)
    assert checks["sku_identity"].verdict == Verdict.UNCERTAIN


def test_unusable_photo_evidence_excluded():
    def m(o):
        o.photos[1].usable = False

    report, checks = run("01_correct_shipment", m)
    assert checks["sku_identity"].verdict == Verdict.UNCERTAIN
    assert any("P2" in w for w in report.warnings)


def test_evidence_refs_carry_photo_hashes():
    report, checks = run("02_short_shipment")
    ev = checks["quantity"].evidence
    assert ev and all(e.sha256 and len(e.sha256) == 64 for e in ev)


def test_sku_not_on_po_raises():
    s = SCENARIOS["01_correct_shipment"]
    try:
        inspect(s.purchase_order, CATALOG, placeholder_photos(s), s.observations, sku="NOPE")
    except ValueError as exc:
        assert "not on purchase order" in str(exc)
    else:
        raise AssertionError("expected ValueError")
