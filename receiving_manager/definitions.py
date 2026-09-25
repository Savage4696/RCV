"""Plain-language definitions of verdicts, decisions and checks, shown in the UI and API."""

VERDICTS = {
    "PASS": "The evidence positively proves the check: a cited, usable photo shows the expected "
    "value with confidence at or above the threshold.",
    "FAIL": "The evidence positively proves a discrepancy: a cited, usable photo shows a value "
    "or defect that contradicts the purchase order or catalogue, with sufficient confidence.",
    "UNCERTAIN": "The evidence is missing, partial, low-confidence or ambiguous, so the check can "
    "be neither proven nor disproven. A person must look. This is never guessed into PASS or FAIL.",
    "NOT_APPLICABLE": "The PO and catalogue define nothing to check (e.g. no components listed).",
}

DECISIONS = {
    "ACCEPT": "Every applicable check PASSED. Receive the stock into inventory.",
    "EXCEPTION": "At least one check FAILED on solid evidence. Hold the stock, record the "
    "discrepancy and raise a claim with the supplier using the evidence record.",
    "UNCERTAIN": "Nothing failed, but at least one check could not be verified. Do not accept "
    "blindly: take more photos or inspect manually, then re-run.",
}

CHECKS = {
    "sku_identity": "A SKU, barcode or ASIN read from a photo matches the PO line. Product "
    "names or look-alike appearance alone are not enough.",
    "variant": "Observed colour/variant attributes match the PO line (or catalogue).",
    "quantity": "Units counted when all are visible, or derived from sealed cartons x pack size "
    "once the SKU is confirmed. A partial count can prove an over-shipment, never a short one.",
    "carton_count": "Number of cartons received vs. expected cartons.",
    "units_per_carton": "Pack size printed on the carton label vs. the expected pack size.",
    "damage": "Crushing, water damage, tears, punctures or other damage. No-damage PASS "
    "requires full photo coverage of every carton/product.",
    "components": "Each catalogue component explicitly seen present; any explicitly seen missing "
    "is a FAIL.",
    "other_quality": "Any other obvious quality problem cited to a photo.",
}


def as_dict() -> dict:
    return {"verdicts": VERDICTS, "decisions": DECISIONS, "checks": CHECKS}
