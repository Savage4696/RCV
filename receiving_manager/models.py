from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Inputs: purchase order, catalogue, photos
# ---------------------------------------------------------------------------


class CatalogItem(BaseModel):
    sku: str
    name: str
    asin: str | None = None
    barcodes: list[str] = Field(default_factory=list, description="UPC/EAN/GTIN codes")
    variant: dict[str, str] = Field(default_factory=dict, description="e.g. {'color': 'blue'}")
    units_per_carton: int | None = None
    components: list[str] = Field(
        default_factory=list, description="Components every unit must include"
    )
    description: str | None = None


class POLine(BaseModel):
    sku: str
    expected_quantity: int = Field(ge=0)
    variant: dict[str, str] = Field(default_factory=dict)
    expected_cartons: int | None = Field(default=None, ge=0)
    units_per_carton: int | None = Field(default=None, ge=1)


class PurchaseOrder(BaseModel):
    po_number: str
    supplier: str | None = None
    lines: list[POLine] = Field(min_length=1)


class PhotoInput(BaseModel):
    photo_id: str
    filename: str
    content_type: str = "image/jpeg"
    sha256: str
    size_bytes: int
    stored_path: str | None = None


# ---------------------------------------------------------------------------
# Observations: what the vision model (or a human inspector) reports seeing.
# Every observation must cite the photo(s) it was seen in.
# ---------------------------------------------------------------------------


class DamageType(str, Enum):
    CRUSH = "crush"
    WATER = "water"
    TEAR = "tear"
    PUNCTURE = "puncture"
    OTHER = "other"


class PhotoReview(BaseModel):
    photo_id: str
    usable: bool
    shows: list[str] = Field(
        default_factory=list, description="e.g. 'carton exterior', 'label', 'open carton'"
    )
    quality_issues: list[str] = Field(default_factory=list)


class IdentifierSighting(BaseModel):
    kind: str = Field(description="sku | barcode | asin | product_name")
    value: str
    photo_ids: list[str]
    confidence: float = Field(ge=0, le=1)


class VariantObservation(BaseModel):
    attributes: dict[str, str] = Field(default_factory=dict)
    photo_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0, ge=0, le=1)
    notes: str | None = None


class CountObservation(BaseModel):
    count: int | None = None
    all_visible: bool | None = Field(
        default=None, description="True only if every item being counted is visible"
    )
    photo_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0, ge=0, le=1)
    notes: str | None = None


class CartonObservation(BaseModel):
    count: CountObservation = Field(default_factory=CountObservation)
    units_per_carton_label: int | None = Field(
        default=None, description="Units per carton as printed on the carton label, if legible"
    )
    units_per_carton_label_photo_ids: list[str] = Field(default_factory=list)
    units_per_carton_label_confidence: float = Field(default=0, ge=0, le=1)
    sealed: bool | None = None

    @field_validator("count", mode="before")
    @classmethod
    def _coerce_bare_count(cls, value: object) -> object:
        if value is None or isinstance(value, int):
            return {"count": value}
        return value


class DamageFinding(BaseModel):
    type: DamageType
    target: str = Field(description="carton | product | packaging")
    description: str
    severity: str = Field(default="unknown", description="minor | moderate | severe | unknown")
    photo_ids: list[str]
    confidence: float = Field(ge=0, le=1)


class ComponentObservation(BaseModel):
    name: str
    present: bool | None = None
    photo_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0, ge=0, le=1)
    notes: str | None = None


class OtherIssue(BaseModel):
    description: str
    photo_ids: list[str]
    confidence: float = Field(ge=0, le=1)


class Observations(BaseModel):
    photos: list[PhotoReview] = Field(default_factory=list)
    identifiers: list[IdentifierSighting] = Field(default_factory=list)
    variant: VariantObservation = Field(default_factory=VariantObservation)
    units: CountObservation = Field(default_factory=CountObservation)
    cartons: CartonObservation = Field(default_factory=CartonObservation)
    damage: list[DamageFinding] = Field(default_factory=list)
    damage_coverage: str = Field(
        default="none",
        description="full = all carton faces/products visible; partial; none",
    )
    components: list[ComponentObservation] = Field(default_factory=list)
    other_issues: list[OtherIssue] = Field(default_factory=list)
    notes: str | None = None


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNCERTAIN = "UNCERTAIN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class Decision(str, Enum):
    ACCEPT = "ACCEPT"
    EXCEPTION = "EXCEPTION"
    UNCERTAIN = "UNCERTAIN"


class EvidenceRef(BaseModel):
    photo_id: str
    sha256: str | None = None
    detail: str


class Issue(BaseModel):
    code: str
    description: str
    photo_ids: list[str] = Field(default_factory=list)


class CheckResult(BaseModel):
    check: str
    verdict: Verdict
    expected: str | int | None = None
    observed: str | int | None = None
    confidence: float | None = None
    reason: str
    issues: list[Issue] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)


class InspectionReport(BaseModel):
    inspection_id: str
    created_at: datetime
    po_number: str
    sku: str
    decision: Decision
    decision_reason: str
    checks: list[CheckResult]
    issues: list[Issue]
    warnings: list[str] = Field(default_factory=list)
    confidence_threshold: float


class EvidenceRecord(BaseModel):
    """Self-contained, tamper-evident record of an inspection."""

    schema_version: str = "1.0"
    report: InspectionReport
    purchase_order: PurchaseOrder
    po_line: POLine
    catalog_item: CatalogItem | None
    photos: list[PhotoInput]
    observations: Observations
    observation_source: str
    record_sha256: str | None = None
