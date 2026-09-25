import uuid
from dataclasses import dataclass

from .engine import find_line, inspect, normalize_id
from .evidence import EvidenceStore, seal
from .models import CatalogItem, EvidenceRecord, Observations, PurchaseOrder
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
        except VisionError as exc:
            observations = Observations()
            source = f"{provider.name} (failed)"
            extra_warnings.append(str(exc))

    report, _ = inspect(
        po,
        catalog,
        photos,
        observations,
        threshold=threshold,
        sku=line.sku,
        inspection_id=inspection_id,
    )
    report.warnings = extra_warnings + report.warnings
    record = seal(
        EvidenceRecord(
            report=report,
            purchase_order=po,
            po_line=line,
            catalog_item=catalog_item,
            photos=photos,
            observations=observations,
            observation_source=source,
        )
    )
    store.save_record(record)
    return record
