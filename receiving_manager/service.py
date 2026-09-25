import uuid
from dataclasses import dataclass

from .engine import find_line, inspect, normalize_id
from .evidence import EvidenceStore, seal
from .models import AIReview, CatalogItem, EvidenceRecord, Observations, PurchaseOrder
from .reasoning import ReasoningError, ReasoningReviewer, apply_review
from .vision import ImagePayload, VisionError, VisionProvider


@dataclass
class UploadedPhoto:
    filename: str
    content_type: str
    data: bytes


def run_inspection(
    po: PurchaseOrder,
    catalog: list[CatalogItem],
    uploads: list[UploadedPhoto],
    store: EvidenceStore,
    provider: VisionProvider | None,
    threshold: float,
    sku: str | None = None,
    observations: Observations | None = None,
    reviewer: ReasoningReviewer | None = None,
) -> EvidenceRecord:
    """Run an inspection.

    If ``observations`` is supplied (e.g. recorded by a human inspector) it is used as-is;
    otherwise the vision provider analyses the photos. With neither, no visual evidence exists
    and the engine returns UNCERTAIN.
    """
    inspection_id = uuid.uuid4().hex[:12]
    line = find_line(po, sku)
    photos = [
        store.save_photo(inspection_id, f"P{i + 1}", u.filename, u.content_type, u.data)
        for i, u in enumerate(uploads)
    ]
    catalog_item = next((c for c in catalog if normalize_id(c.sku) == normalize_id(line.sku)), None)
    extra_warnings: list[str] = []
    llm_cost = 0.0
    if observations is not None:
        source = "precomputed"
    elif provider is None:
        observations = Observations()
        source = "none"
        extra_warnings.append("No vision provider configured and no observations supplied")
    elif not photos:
        observations = Observations()
        source = provider.name
        extra_warnings.append("No photos supplied")
    else:
        images = [
            ImagePayload(p.photo_id, p.content_type, u.data)
            for p, u in zip(photos, uploads, strict=True)
        ]
        try:
            observations = provider.observe(line, catalog_item, images)
            source = provider.name
            last_call = provider.last_call
            if last_call is not None:
                llm_cost += last_call.cost_usd
                if last_call.cached:
                    source += " (cached)"
        except VisionError as exc:
            observations = Observations()
            source = f"{provider.name} (failed)"
            extra_warnings.append(str(exc))

    report, clean = inspect(
        po,
        catalog,
        photos,
        observations,
        threshold=threshold,
        sku=line.sku,
        inspection_id=inspection_id,
    )
    report.warnings = extra_warnings + report.warnings
    review: AIReview | None = None
    if reviewer is not None:
        try:
            review = reviewer.review(
                report, line, catalog_item, clean, [p.photo_id for p in photos]
            )
            llm_cost += review.cost_usd
            apply_review(report, review)
        except ReasoningError as exc:
            report.warnings.append(f"Reasoning review unavailable: {exc}")
    record = seal(
        EvidenceRecord(
            report=report,
            purchase_order=po,
            po_line=line,
            catalog_item=catalog_item,
            photos=photos,
            observations=observations,
            observation_source=source,
            ai_review=review,
            llm_cost_usd=round(llm_cost, 6),
        )
    )
    store.save_record(record)
    return record
