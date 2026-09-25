import json
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import TypeAdapter, ValidationError

from . import definitions
from .config import load_settings
from .engine import inspect
from .evidence import EvidenceStore, verify
from .models import CatalogItem, EvidenceRecord, Observations, PurchaseOrder
from .scenarios import SCENARIO_DIR, load_catalog, load_scenarios, placeholder_photos
from .service import UploadedPhoto, run_inspection
from .vision import get_provider, get_reviewer, make_budget, make_cache

STATIC_DIR = Path(__file__).parent / "static"
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

settings = load_settings()
store = EvidenceStore(settings.data_dir)
budget = make_budget(settings)
cache = make_cache(settings)
provider = get_provider(settings, budget, cache)
reviewer = get_reviewer(settings, budget, cache)
app = FastAPI(title="Receiving Manager")
catalog_adapter = TypeAdapter(list[CatalogItem])
NO_PHOTOS = File(default=[])


def _parse(model, raw: str, field: str):
    try:
        if isinstance(model, TypeAdapter):
            return model.validate_json(raw)
        return model.model_validate_json(raw)
    except (ValidationError, json.JSONDecodeError) as exc:
        raise HTTPException(422, f"Invalid {field}: {exc}") from exc


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "vision_provider": provider.name if provider else None,
        "reasoning_model": reviewer.model if reviewer else None,
        "confidence_threshold": settings.confidence_threshold,
    }


@app.get("/api/budget")
def budget_status() -> dict:
    return budget.status()


@app.get("/api/definitions")
def get_definitions() -> dict:
    return definitions.as_dict()


@app.get("/api/benchmark")
def benchmark() -> dict:
    """Run every bundled scenario through the rules engine (no model calls, no cost)."""
    catalog = load_catalog()
    rows = []
    for s in load_scenarios():
        report, _ = inspect(
            s.purchase_order,
            catalog,
            placeholder_photos(s),
            s.observations,
            threshold=settings.confidence_threshold,
        )
        verdicts = {c.check: c.verdict.value for c in report.checks}
        codes = {i.code for i in report.issues}
        check_ok = all(verdicts.get(k) == v for k, v in s.expected.checks.items())
        rows.append(
            {
                "name": s.name,
                "title": s.title,
                "expected_decision": s.expected.decision,
                "decision": report.decision.value,
                "verdicts": verdicts,
                "issue_codes": sorted(codes),
                "passed": report.decision.value == s.expected.decision
                and check_ok
                and set(s.expected.issue_codes) <= codes,
            }
        )
    return {"total": len(rows), "passed": sum(r["passed"] for r in rows), "results": rows}


@app.get("/api/scenarios/{name}/photos/{photo_id}")
def scenario_photo(name: str, photo_id: str) -> FileResponse:
    names = {s.name for s in load_scenarios()}
    path = SCENARIO_DIR / "photos" / name / f"{photo_id}.png"
    if name not in names or not path.resolve().is_relative_to(SCENARIO_DIR) or not path.exists():
        raise HTTPException(404, "Sample photo not found")
    return FileResponse(path, media_type="image/png")


@app.get("/api/scenarios")
def scenarios() -> dict:
    return {
        "catalog": [c.model_dump() for c in load_catalog()],
        "scenarios": [s.model_dump(mode="json") for s in load_scenarios()],
    }


@app.post("/api/inspections")
async def create_inspection(
    purchase_order: str = Form(...),
    catalog: str = Form("[]"),
    sku: str | None = Form(None),
    observations: str | None = Form(None),
    ai_review: bool = Form(True),
    photos: list[UploadFile] = NO_PHOTOS,
) -> EvidenceRecord:
    po = _parse(PurchaseOrder, purchase_order, "purchase_order")
    cat = _parse(catalog_adapter, catalog, "catalog")
    obs = _parse(Observations, observations, "observations") if observations else None
    uploads = []
    for f in photos:
        if f.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(415, f"Unsupported photo type for {f.filename}: {f.content_type}")
        uploads.append(UploadedPhoto(f.filename or "photo", f.content_type, await f.read()))
    try:
        return run_inspection(
            po,
            cat,
            uploads,
            store,
            provider,
            settings.confidence_threshold,
            sku=sku or None,
            observations=obs,
            reviewer=reviewer if ai_review else None,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/inspections")
def list_inspections() -> list[dict]:
    return [
        {
            "inspection_id": r.report.inspection_id,
            "created_at": r.report.created_at,
            "po_number": r.report.po_number,
            "sku": r.report.sku,
            "decision": r.report.decision,
        }
        for r in store.list_records()
    ]


def _load(inspection_id: str) -> EvidenceRecord:
    try:
        record = store.load_record(inspection_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if record is None:
        raise HTTPException(404, "Inspection not found")
    return record


@app.get("/api/inspections/{inspection_id}")
def get_inspection(inspection_id: str) -> EvidenceRecord:
    return _load(inspection_id)


@app.get("/api/inspections/{inspection_id}/verify")
def verify_inspection(inspection_id: str) -> dict:
    record = _load(inspection_id)
    return {
        "inspection_id": inspection_id,
        "record_sha256": record.record_sha256,
        "valid": verify(record),
    }


@app.get("/api/inspections/{inspection_id}/photos/{photo_id}")
def get_photo(inspection_id: str, photo_id: str) -> FileResponse:
    record = _load(inspection_id)
    photo = next((p for p in record.photos if p.photo_id == photo_id), None)
    if photo is None or not photo.stored_path or not Path(photo.stored_path).exists():
        raise HTTPException(404, "Photo not found")
    return FileResponse(photo.stored_path, media_type=photo.content_type)
