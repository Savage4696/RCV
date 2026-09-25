"""Deterministic rules engine.

Turns evidence-cited observations into PASS / FAIL / UNCERTAIN verdicts. The engine never
guesses: an observation only counts if it cites at least one submitted, usable photo and meets
the confidence threshold. Anything else leads to UNCERTAIN.
"""

import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from .models import (
    CatalogItem,
    CheckResult,
    CountObservation,
    DamageType,
    Decision,
    EvidenceRef,
    InspectionReport,
    Issue,
    Observations,
    PhotoInput,
    POLine,
    PurchaseOrder,
    Verdict,
)

DAMAGE_CODES = {
    DamageType.CRUSH: "CRUSHED",
    DamageType.WATER: "WATER_DAMAGE",
    DamageType.TEAR: "TORN",
    DamageType.PUNCTURE: "PUNCTURED",
    DamageType.OTHER: "OTHER_DAMAGE",
}
STRONG_IDENTIFIER_KINDS = {"sku", "barcode", "asin"}


def normalize_id(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def normalize_attr(value: str) -> str:
    return value.strip().lower()


@dataclass
class InspectionContext:
    po: PurchaseOrder
    line: POLine
    catalog_item: CatalogItem | None
    catalog: list[CatalogItem]
    photos: dict[str, PhotoInput]
    obs: Observations
    threshold: float
    sku_confirmed: bool = False

    @property
    def units_per_carton(self) -> int | None:
        if self.line.units_per_carton:
            return self.line.units_per_carton
        return self.catalog_item.units_per_carton if self.catalog_item else None

    @property
    def expected_cartons(self) -> int | None:
        if self.line.expected_cartons is not None:
            return self.line.expected_cartons
        upc = self.units_per_carton
        return math.ceil(self.line.expected_quantity / upc) if upc else None

    @property
    def expected_variant(self) -> dict[str, str]:
        if self.line.variant:
            return self.line.variant
        return self.catalog_item.variant if self.catalog_item else {}

    def confident(self, confidence: float) -> bool:
        return confidence >= self.threshold

    def refs(self, photo_ids: list[str], detail: str) -> list[EvidenceRef]:
        return [
            EvidenceRef(
                photo_id=p,
                sha256=self.photos[p].sha256 if p in self.photos else None,
                detail=detail,
            )
            for p in photo_ids
        ]


# ---------------------------------------------------------------------------
# Evidence sanitisation: drop anything that does not cite a real, usable photo.
# ---------------------------------------------------------------------------


def sanitize(obs: Observations, photos: dict[str, PhotoInput]) -> tuple[Observations, list[str]]:
    warnings: list[str] = []
    unusable = {p.photo_id for p in obs.photos if not p.usable}
    valid = set(photos) - unusable
    obs = obs.model_copy(deep=True)

    def keep(ids: list[str], what: str) -> list[str]:
        unknown = [i for i in ids if i not in photos]
        if unknown:
            warnings.append(f"{what}: cited unknown photo(s) {unknown}; citation ignored")
        return [i for i in ids if i in valid]

    for review in obs.photos:
        if review.photo_id not in photos:
            warnings.append(f"Observation reviewed unknown photo '{review.photo_id}'")
    for pid in sorted(unusable & set(photos)):
        warnings.append(f"Photo '{pid}' marked unusable; its evidence is excluded")
    if not valid:
        warnings.append("No usable photos: no visual evidence is available")

    kept_ids = []
    for s in obs.identifiers:
        s.photo_ids = keep(s.photo_ids, f"Identifier '{s.value}'")
        if s.photo_ids:
            kept_ids.append(s)
        else:
            warnings.append(f"Identifier '{s.value}' discarded: no valid photo evidence")
    obs.identifiers = kept_ids

    obs.variant.photo_ids = keep(obs.variant.photo_ids, "Variant")
    if obs.variant.attributes and not obs.variant.photo_ids:
        warnings.append("Variant observation discarded: no valid photo evidence")
        obs.variant.attributes = {}

    def clean_count(c: CountObservation, what: str) -> None:
        c.photo_ids = keep(c.photo_ids, what)
        if c.count is not None and not c.photo_ids:
            warnings.append(f"{what} discarded: no valid photo evidence")
            c.count = None

    clean_count(obs.units, "Unit count")
    clean_count(obs.cartons.count, "Carton count")
    obs.cartons.units_per_carton_label_photo_ids = keep(
        obs.cartons.units_per_carton_label_photo_ids, "Units-per-carton label"
    )
    if obs.cartons.units_per_carton_label is not None and not (
        obs.cartons.units_per_carton_label_photo_ids
    ):
        warnings.append("Units-per-carton label discarded: no valid photo evidence")
        obs.cartons.units_per_carton_label = None

    kept_damage = []
    for d in obs.damage:
        d.photo_ids = keep(d.photo_ids, f"Damage '{d.description}'")
        if d.photo_ids:
            kept_damage.append(d)
        else:
            warnings.append(f"Damage '{d.description}' discarded: no valid photo evidence")
    obs.damage = kept_damage

    for c in obs.components:
        c.photo_ids = keep(c.photo_ids, f"Component '{c.name}'")
        if c.present is not None and not c.photo_ids:
            warnings.append(f"Component '{c.name}' observation discarded: no valid photo evidence")
            c.present = None

    kept_other = []
    for o in obs.other_issues:
        o.photo_ids = keep(o.photo_ids, f"Issue '{o.description}'")
        if o.photo_ids:
            kept_other.append(o)
        else:
            warnings.append(f"Issue '{o.description}' discarded: no valid photo evidence")
    obs.other_issues = kept_other

    if not valid:
        obs.damage_coverage = "none"
    return obs, warnings


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_sku(ctx: InspectionContext) -> CheckResult:
    expected = {normalize_id(ctx.line.sku)}
    if ctx.catalog_item:
        if ctx.catalog_item.asin:
            expected.add(normalize_id(ctx.catalog_item.asin))
        expected.update(normalize_id(b) for b in ctx.catalog_item.barcodes)

    other_items = {}
    for item in ctx.catalog:
        if normalize_id(item.sku) == normalize_id(ctx.line.sku):
            continue
        for ident in [item.sku, item.asin, *item.barcodes]:
            if ident:
                other_items[normalize_id(ident)] = item.sku

    matches, foreign, unrecognised, weak = [], [], [], []
    for s in ctx.obs.identifiers:
        if s.kind not in STRONG_IDENTIFIER_KINDS:
            weak.append(s)
            continue
        if not ctx.confident(s.confidence):
            weak.append(s)
            continue
        norm = normalize_id(s.value)
        if norm in expected:
            matches.append(s)
        elif norm in other_items or s.kind in {"sku", "asin"}:
            foreign.append(s)
        else:
            unrecognised.append(s)

    evidence = []
    for s in matches + foreign + unrecognised:
        evidence += ctx.refs(s.photo_ids, f"{s.kind} '{s.value}' read (conf {s.confidence:.2f})")

    def describe(s):
        known = other_items.get(normalize_id(s.value))
        return f"{s.kind} '{s.value}'" + (f" (catalogue SKU {known})" if known else "")

    if foreign:
        photo_ids = sorted({p for s in foreign for p in s.photo_ids})
        observed = ", ".join(describe(s) for s in foreign)
        if matches:
            return CheckResult(
                check="sku_identity",
                verdict=Verdict.FAIL,
                expected=ctx.line.sku,
                observed=f"{ctx.line.sku} + {observed}",
                confidence=min(s.confidence for s in foreign),
                reason="Expected SKU present, but a different SKU is also present (mixed shipment)",
                issues=[
                    Issue(
                        code="MIXED_SKU",
                        description=f"Foreign item(s): {observed}",
                        photo_ids=photo_ids,
                    )
                ],
                evidence=evidence,
            )
        return CheckResult(
            check="sku_identity",
            verdict=Verdict.FAIL,
            expected=ctx.line.sku,
            observed=observed,
            confidence=min(s.confidence for s in foreign),
            reason="Identifiers read on the shipment do not match the ordered SKU",
            issues=[
                Issue(
                    code="WRONG_SKU",
                    description=f"Received {observed} instead of " f"{ctx.line.sku}",
                    photo_ids=photo_ids,
                )
            ],
            evidence=evidence,
        )
    if matches:
        issues = [
            Issue(
                code="UNRECOGNISED_IDENTIFIER",
                description=f"Unrecognised {s.kind} '{s.value}'",
                photo_ids=s.photo_ids,
            )
            for s in unrecognised
        ]
        return CheckResult(
            check="sku_identity",
            verdict=Verdict.PASS,
            expected=ctx.line.sku,
            observed=", ".join(f"{s.kind} '{s.value}'" for s in matches),
            confidence=max(s.confidence for s in matches),
            reason="Ordered SKU identifier read on the shipment",
            issues=issues,
            evidence=evidence,
        )
    if unrecognised:
        return CheckResult(
            check="sku_identity",
            verdict=Verdict.UNCERTAIN,
            expected=ctx.line.sku,
            observed=", ".join(f"{s.kind} '{s.value}'" for s in unrecognised),
            reason="Only identifiers not found in the catalogue were read; cannot confirm SKU",
            issues=[
                Issue(
                    code="SKU_UNVERIFIED",
                    description="Identifier not in catalogue",
                    photo_ids=sorted({p for s in unrecognised for p in s.photo_ids}),
                )
            ],
            evidence=evidence,
        )
    weak_desc = "; ".join(f"{s.kind} '{s.value}' (conf {s.confidence:.2f})" for s in weak)
    return CheckResult(
        check="sku_identity",
        verdict=Verdict.UNCERTAIN,
        expected=ctx.line.sku,
        observed=weak_desc or None,
        reason="No legible SKU/barcode/ASIN with sufficient confidence; visual resemblance alone "
        "is not accepted as identification",
        issues=[
            Issue(code="SKU_UNVERIFIED", description="Product identity could not be confirmed")
        ],
        evidence=[r for s in weak for r in ctx.refs(s.photo_ids, f"low-confidence {s.kind}")],
    )


def check_variant(ctx: InspectionContext) -> CheckResult:
    expected = {k.lower(): normalize_attr(v) for k, v in ctx.expected_variant.items()}
    if not expected:
        return CheckResult(
            check="variant",
            verdict=Verdict.NOT_APPLICABLE,
            reason="No variant specified on PO or catalogue",
        )
    v = ctx.obs.variant
    observed = {k.lower(): normalize_attr(val) for k, val in v.attributes.items()}
    exp_str = ", ".join(f"{k}={val}" for k, val in expected.items())
    obs_str = ", ".join(f"{k}={val}" for k, val in observed.items()) or None
    evidence = ctx.refs(v.photo_ids, f"variant observed: {obs_str} (conf {v.confidence:.2f})")
    if not observed:
        return CheckResult(
            check="variant",
            verdict=Verdict.UNCERTAIN,
            expected=exp_str,
            reason="Variant not visible in the photos",
            issues=[Issue(code="VARIANT_UNVERIFIED", description="Variant could not be observed")],
        )
    if not ctx.confident(v.confidence):
        return CheckResult(
            check="variant",
            verdict=Verdict.UNCERTAIN,
            expected=exp_str,
            observed=obs_str,
            confidence=v.confidence,
            reason=f"Variant observation confidence {v.confidence:.2f} below threshold",
            issues=[
                Issue(
                    code="VARIANT_UNVERIFIED",
                    description="Variant ambiguous in photos",
                    photo_ids=v.photo_ids,
                )
            ],
            evidence=evidence,
        )
    mismatched = {
        k: (e, observed[k]) for k, e in expected.items() if k in observed and observed[k] != e
    }
    missing = [k for k in expected if k not in observed]
    if mismatched:
        desc = "; ".join(f"{k}: expected {e}, observed {o}" for k, (e, o) in mismatched.items())
        return CheckResult(
            check="variant",
            verdict=Verdict.FAIL,
            expected=exp_str,
            observed=obs_str,
            confidence=v.confidence,
            reason="Observed variant differs from the order",
            issues=[Issue(code="WRONG_VARIANT", description=desc, photo_ids=v.photo_ids)],
            evidence=evidence,
        )
    if missing:
        return CheckResult(
            check="variant",
            verdict=Verdict.UNCERTAIN,
            expected=exp_str,
            observed=obs_str,
            confidence=v.confidence,
            reason=f"Could not observe variant attribute(s): {', '.join(missing)}",
            issues=[
                Issue(
                    code="VARIANT_UNVERIFIED",
                    description=f"Unobserved: {missing}",
                    photo_ids=v.photo_ids,
                )
            ],
            evidence=evidence,
        )
    return CheckResult(
        check="variant",
        verdict=Verdict.PASS,
        expected=exp_str,
        observed=obs_str,
        confidence=v.confidence,
        reason="Observed variant matches the order",
        evidence=evidence,
    )


def _label_upc(ctx: InspectionContext) -> int | None:
    c = ctx.obs.cartons
    if c.units_per_carton_label is not None and ctx.confident(c.units_per_carton_label_confidence):
        return c.units_per_carton_label
    return None


def check_quantity(ctx: InspectionContext) -> CheckResult:
    expected = ctx.line.expected_quantity
    units = ctx.obs.units
    cartons = ctx.obs.cartons

    observed: int | None = None
    confidence: float | None = None
    basis = ""
    photo_ids: list[str] = []

    if units.count is not None and units.all_visible and ctx.confident(units.confidence):
        observed, confidence, photo_ids = units.count, units.confidence, units.photo_ids
        basis = "direct count of units (all units visible)"
    elif (
        cartons.count.count is not None
        and cartons.count.all_visible
        and ctx.confident(cartons.count.confidence)
        and cartons.sealed is True
        and ctx.sku_confirmed
        and (_label_upc(ctx) or ctx.units_per_carton)
    ):
        label = _label_upc(ctx)
        upc = label or ctx.units_per_carton
        observed = cartons.count.count * upc
        conf_parts = [cartons.count.confidence]
        if label:
            conf_parts.append(cartons.units_per_carton_label_confidence)
        confidence = round(min(conf_parts) * 0.9, 2)
        photo_ids = cartons.count.photo_ids + (
            cartons.units_per_carton_label_photo_ids if label else []
        )
        basis = (
            f"derived: {cartons.count.count} sealed cartons x {upc} units/carton "
            f"({'carton label' if label else 'catalogue/PO'}); contents not visually verified"
        )

    if observed is not None:
        evidence = ctx.refs(photo_ids, basis)
        if observed == expected:
            return CheckResult(
                check="quantity",
                verdict=Verdict.PASS,
                expected=expected,
                observed=observed,
                confidence=confidence,
                reason=f"Quantity matches ({basis})",
                evidence=evidence,
            )
        short = observed < expected
        diff = abs(expected - observed)
        return CheckResult(
            check="quantity",
            verdict=Verdict.FAIL,
            expected=expected,
            observed=observed,
            confidence=confidence,
            reason=f"{'Short' if short else 'Over'} by {diff} unit(s) ({basis})",
            issues=[
                Issue(
                    code="SHORT_SHIPMENT" if short else "OVER_SHIPMENT",
                    description=f"Expected {expected}, observed {observed}",
                    photo_ids=photo_ids,
                )
            ],
            evidence=evidence,
        )

    if units.count is not None and ctx.confident(units.confidence) and units.count > expected:
        return CheckResult(
            check="quantity",
            verdict=Verdict.FAIL,
            expected=expected,
            observed=f">= {units.count}",
            confidence=units.confidence,
            reason=f"At least {units.count} units visible, exceeding the ordered {expected}",
            issues=[
                Issue(
                    code="OVER_SHIPMENT",
                    description=f"At least {units.count - expected} " "extra unit(s)",
                    photo_ids=units.photo_ids,
                )
            ],
            evidence=ctx.refs(units.photo_ids, "partial unit count (lower bound)"),
        )

    reasons = []
    if units.count is None:
        reasons.append("units could not be counted")
    elif not units.all_visible:
        reasons.append(f"only {units.count} units visible (not all units visible)")
    else:
        reasons.append(f"unit count confidence {units.confidence:.2f} below threshold")
    if cartons.count.count is not None and cartons.sealed is not True:
        reasons.append("cartons not confirmed sealed, so carton-based count not used")
    elif cartons.count.count is not None and not ctx.sku_confirmed:
        reasons.append("product identity not confirmed, so carton contents cannot be assumed")
    observed_str = f">= {units.count}" if units.count is not None else None
    return CheckResult(
        check="quantity",
        verdict=Verdict.UNCERTAIN,
        expected=expected,
        observed=observed_str,
        confidence=(units.confidence or None) if units.count is not None else None,
        reason="Quantity cannot be verified: " + "; ".join(reasons),
        issues=[
            Issue(
                code="QUANTITY_UNVERIFIED",
                description="Quantity not verifiable from photos",
                photo_ids=units.photo_ids,
            )
        ],
        evidence=ctx.refs(units.photo_ids, "partial unit count"),
    )


def check_carton_count(ctx: InspectionContext) -> CheckResult:
    expected = ctx.expected_cartons
    c = ctx.obs.cartons.count
    evidence = ctx.refs(c.photo_ids, f"cartons counted: {c.count} (conf {c.confidence:.2f})")
    if expected is None:
        return CheckResult(
            check="carton_count",
            verdict=Verdict.NOT_APPLICABLE,
            observed=c.count,
            reason="No expected carton count on PO/catalogue",
            evidence=evidence,
        )
    if c.count is not None and c.all_visible and ctx.confident(c.confidence):
        if c.count == expected:
            return CheckResult(
                check="carton_count",
                verdict=Verdict.PASS,
                expected=expected,
                observed=c.count,
                confidence=c.confidence,
                reason="Carton count matches",
                evidence=evidence,
            )
        return CheckResult(
            check="carton_count",
            verdict=Verdict.FAIL,
            expected=expected,
            observed=c.count,
            confidence=c.confidence,
            reason="Carton count differs from expected",
            issues=[
                Issue(
                    code="CARTON_COUNT_MISMATCH",
                    description=f"Expected {expected} cartons, observed {c.count}",
                    photo_ids=c.photo_ids,
                )
            ],
            evidence=evidence,
        )
    if c.count is not None and ctx.confident(c.confidence) and c.count > expected:
        return CheckResult(
            check="carton_count",
            verdict=Verdict.FAIL,
            expected=expected,
            observed=f">= {c.count}",
            confidence=c.confidence,
            reason="More cartons visible than expected",
            issues=[
                Issue(
                    code="CARTON_COUNT_MISMATCH",
                    description=f"At least {c.count} cartons vs {expected} expected",
                    photo_ids=c.photo_ids,
                )
            ],
            evidence=evidence,
        )
    return CheckResult(
        check="carton_count",
        verdict=Verdict.UNCERTAIN,
        expected=expected,
        observed=c.count,
        confidence=c.confidence or None,
        reason="Carton count not verifiable (not all cartons visible or low confidence)",
        issues=[
            Issue(
                code="CARTON_COUNT_UNVERIFIED",
                description="Carton count not verifiable",
                photo_ids=c.photo_ids,
            )
        ],
        evidence=evidence,
    )


def check_units_per_carton(ctx: InspectionContext) -> CheckResult:
    expected = ctx.units_per_carton
    c = ctx.obs.cartons
    if expected is None:
        return CheckResult(
            check="units_per_carton",
            verdict=Verdict.NOT_APPLICABLE,
            observed=c.units_per_carton_label,
            reason="No units-per-carton specified on PO/catalogue",
        )
    evidence = ctx.refs(
        c.units_per_carton_label_photo_ids, f"carton label: {c.units_per_carton_label} units/carton"
    )
    label = _label_upc(ctx)
    if label is None:
        return CheckResult(
            check="units_per_carton",
            verdict=Verdict.UNCERTAIN,
            expected=expected,
            observed=c.units_per_carton_label,
            confidence=c.units_per_carton_label_confidence or None,
            reason="Units-per-carton label not legible with sufficient confidence",
            issues=[
                Issue(
                    code="UNITS_PER_CARTON_UNVERIFIED",
                    description="Carton label quantity not readable",
                    photo_ids=c.units_per_carton_label_photo_ids,
                )
            ],
            evidence=evidence,
        )
    if label == expected:
        return CheckResult(
            check="units_per_carton",
            verdict=Verdict.PASS,
            expected=expected,
            observed=label,
            confidence=c.units_per_carton_label_confidence,
            reason="Carton label matches expected pack size",
            evidence=evidence,
        )
    return CheckResult(
        check="units_per_carton",
        verdict=Verdict.FAIL,
        expected=expected,
        observed=label,
        confidence=c.units_per_carton_label_confidence,
        reason="Carton label pack size differs from expected",
        issues=[
            Issue(
                code="UNITS_PER_CARTON_MISMATCH",
                description=f"Expected {expected}/carton, label shows {label}",
                photo_ids=c.units_per_carton_label_photo_ids,
            )
        ],
        evidence=evidence,
    )


def check_damage(ctx: InspectionContext) -> CheckResult:
    confident = [d for d in ctx.obs.damage if ctx.confident(d.confidence)]
    possible = [d for d in ctx.obs.damage if not ctx.confident(d.confidence)]
    evidence = [
        r
        for d in ctx.obs.damage
        for r in ctx.refs(
            d.photo_ids,
            f"{d.type.value} on {d.target} ({d.severity}): "
            f"{d.description} (conf {d.confidence:.2f})",
        )
    ]
    if confident:
        issues = [
            Issue(
                code=DAMAGE_CODES[d.type],
                description=f"{d.type.value} on {d.target} ({d.severity}): {d.description}",
                photo_ids=d.photo_ids,
            )
            for d in confident
        ]
        return CheckResult(
            check="damage",
            verdict=Verdict.FAIL,
            expected="no visible damage",
            observed=", ".join(sorted({f"{d.type.value} ({d.target})" for d in confident})),
            confidence=max(d.confidence for d in confident),
            reason=f"{len(confident)} damage finding(s) above confidence threshold",
            issues=issues,
            evidence=evidence,
        )
    if possible:
        return CheckResult(
            check="damage",
            verdict=Verdict.UNCERTAIN,
            expected="no visible damage",
            observed="possible " + ", ".join(sorted({d.type.value for d in possible})),
            confidence=max(d.confidence for d in possible),
            reason="Possible damage seen but below confidence threshold; manual inspection needed",
            issues=[
                Issue(code="POSSIBLE_DAMAGE", description=d.description, photo_ids=d.photo_ids)
                for d in possible
            ],
            evidence=evidence,
        )
    if ctx.obs.damage_coverage == "full":
        return CheckResult(
            check="damage",
            verdict=Verdict.PASS,
            expected="no visible damage",
            observed="no damage observed",
            reason="No damage observed with full photo coverage",
        )
    return CheckResult(
        check="damage",
        verdict=Verdict.UNCERTAIN,
        expected="no visible damage",
        observed="no damage observed",
        reason=f"No damage observed, but photo coverage is '{ctx.obs.damage_coverage}'; "
        "absence of evidence is not evidence of absence",
        issues=[
            Issue(code="DAMAGE_COVERAGE_INCOMPLETE", description="Not all cartons/products visible")
        ],
    )


def check_components(ctx: InspectionContext) -> CheckResult:
    expected = ctx.catalog_item.components if ctx.catalog_item else []
    if not expected:
        return CheckResult(
            check="components",
            verdict=Verdict.NOT_APPLICABLE,
            reason="No components defined in catalogue",
        )
    by_name = {normalize_attr(c.name): c for c in ctx.obs.components}
    missing, present, unknown, evidence = [], [], [], []
    for name in expected:
        c = by_name.get(normalize_attr(name))
        if c is None or c.present is None or not ctx.confident(c.confidence):
            unknown.append(name)
            if c and c.photo_ids:
                evidence += ctx.refs(c.photo_ids, f"{name}: not determinable")
            continue
        (present if c.present else missing).append((name, c))
        evidence += ctx.refs(
            c.photo_ids,
            f"{name}: {'present' if c.present else 'MISSING'} " f"(conf {c.confidence:.2f})",
        )
    exp_str = ", ".join(expected)
    obs_str = ", ".join(n for n, _ in present) or None
    if missing:
        return CheckResult(
            check="components",
            verdict=Verdict.FAIL,
            expected=exp_str,
            observed=obs_str,
            confidence=min(c.confidence for _, c in missing),
            reason=f"Missing: {', '.join(n for n, _ in missing)}",
            issues=[
                Issue(code="MISSING_COMPONENT", description=f"{n} missing", photo_ids=c.photo_ids)
                for n, c in missing
            ],
            evidence=evidence,
        )
    if unknown:
        return CheckResult(
            check="components",
            verdict=Verdict.UNCERTAIN,
            expected=exp_str,
            observed=obs_str,
            reason=f"Could not verify: {', '.join(unknown)}",
            issues=[Issue(code="COMPONENTS_UNVERIFIED", description=f"Unverified: {unknown}")],
            evidence=evidence,
        )
    return CheckResult(
        check="components",
        verdict=Verdict.PASS,
        expected=exp_str,
        observed=obs_str,
        confidence=min(c.confidence for _, c in present),
        reason="All expected components observed",
        evidence=evidence,
    )


def check_other_quality(ctx: InspectionContext) -> CheckResult:
    confident = [o for o in ctx.obs.other_issues if ctx.confident(o.confidence)]
    possible = [o for o in ctx.obs.other_issues if not ctx.confident(o.confidence)]
    evidence = [
        r
        for o in ctx.obs.other_issues
        for r in ctx.refs(o.photo_ids, f"{o.description} (conf {o.confidence:.2f})")
    ]
    if confident:
        return CheckResult(
            check="other_quality",
            verdict=Verdict.FAIL,
            observed="; ".join(o.description for o in confident),
            confidence=max(o.confidence for o in confident),
            reason="Other quality issues observed",
            issues=[
                Issue(code="QUALITY_ISSUE", description=o.description, photo_ids=o.photo_ids)
                for o in confident
            ],
            evidence=evidence,
        )
    if possible:
        return CheckResult(
            check="other_quality",
            verdict=Verdict.UNCERTAIN,
            observed="; ".join(o.description for o in possible),
            confidence=max(o.confidence for o in possible),
            reason="Possible quality issues below confidence threshold",
            issues=[
                Issue(
                    code="POSSIBLE_QUALITY_ISSUE", description=o.description, photo_ids=o.photo_ids
                )
                for o in possible
            ],
            evidence=evidence,
        )
    return CheckResult(
        check="other_quality",
        verdict=Verdict.PASS,
        observed="none observed",
        reason="No other quality issues observed",
    )


CHECKS = [
    check_sku,
    check_variant,
    check_quantity,
    check_carton_count,
    check_units_per_carton,
    check_damage,
    check_components,
    check_other_quality,
]


def decide(checks: list[CheckResult]) -> tuple[Decision, str]:
    failed = [c.check for c in checks if c.verdict == Verdict.FAIL]
    uncertain = [c.check for c in checks if c.verdict == Verdict.UNCERTAIN]
    if failed:
        reason = f"Failed checks: {', '.join(failed)}"
        if uncertain:
            reason += f". Also unverified: {', '.join(uncertain)}"
        return Decision.EXCEPTION, reason
    if uncertain:
        return Decision.UNCERTAIN, (
            f"No failures, but evidence is insufficient for: {', '.join(uncertain)}. "
            "Manual review required"
        )
    return Decision.ACCEPT, "All checks passed"


def find_line(po: PurchaseOrder, sku: str | None) -> POLine:
    if sku is None:
        return po.lines[0]
    for line in po.lines:
        if normalize_id(line.sku) == normalize_id(sku):
            return line
    raise ValueError(f"SKU '{sku}' is not on purchase order {po.po_number}")


def inspect(
    po: PurchaseOrder,
    catalog: list[CatalogItem],
    photos: list[PhotoInput],
    observations: Observations,
    threshold: float = 0.7,
    sku: str | None = None,
    inspection_id: str | None = None,
) -> tuple[InspectionReport, Observations]:
    line = find_line(po, sku)
    catalog_item = next((c for c in catalog if normalize_id(c.sku) == normalize_id(line.sku)), None)
    photo_map = {p.photo_id: p for p in photos}
    clean, warnings = sanitize(observations, photo_map)
    if catalog_item is None:
        warnings.append(f"SKU {line.sku} not found in product catalogue")
    ctx = InspectionContext(
        po=po,
        line=line,
        catalog_item=catalog_item,
        catalog=catalog,
        photos=photo_map,
        obs=clean,
        threshold=threshold,
    )
    sku_result = check_sku(ctx)
    ctx.sku_confirmed = sku_result.verdict == Verdict.PASS
    checks = [sku_result, *(fn(ctx) for fn in CHECKS if fn is not check_sku)]
    decision, reason = decide(checks)
    report = InspectionReport(
        inspection_id=inspection_id or uuid.uuid4().hex[:12],
        created_at=datetime.now(timezone.utc),
        po_number=po.po_number,
        sku=line.sku,
        decision=decision,
        decision_reason=reason,
        checks=checks,
        issues=[i for c in checks for i in c.issues],
        warnings=warnings,
        confidence_threshold=threshold,
    )
    return report, clean
