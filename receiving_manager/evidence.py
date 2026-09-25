import hashlib
import json
import re
from pathlib import Path

from .models import EvidenceRecord, PhotoInput


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def record_digest(record: EvidenceRecord) -> str:
    payload = record.model_dump(mode="json", exclude={"record_sha256"})
    return sha256_bytes(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())


def seal(record: EvidenceRecord) -> EvidenceRecord:
    record.record_sha256 = record_digest(record)
    return record


def verify(record: EvidenceRecord) -> bool:
    return record.record_sha256 == record_digest(record)


class EvidenceStore:
    def __init__(self, root: Path):
        self.root = root

    def _dir(self, inspection_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", inspection_id):
            raise ValueError("invalid inspection id")
        return self.root / "inspections" / inspection_id

    def save_photo(
        self, inspection_id: str, photo_id: str, filename: str, content_type: str, data: bytes
    ) -> PhotoInput:
        d = self._dir(inspection_id) / "photos"
        d.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name) or photo_id
        path = d / f"{photo_id}__{safe}"
        path.write_bytes(data)
        return PhotoInput(
            photo_id=photo_id,
            filename=filename,
            content_type=content_type,
            sha256=sha256_bytes(data),
            size_bytes=len(data),
            stored_path=str(path),
        )

    def save_record(self, record: EvidenceRecord) -> Path:
        d = self._dir(record.report.inspection_id)
        d.mkdir(parents=True, exist_ok=True)
        path = d / "evidence.json"
        path.write_text(record.model_dump_json(indent=2))
        return path

    def load_record(self, inspection_id: str) -> EvidenceRecord | None:
        path = self._dir(inspection_id) / "evidence.json"
        if not path.exists():
            return None
        return EvidenceRecord.model_validate_json(path.read_text())

    def list_records(self) -> list[EvidenceRecord]:
        base = self.root / "inspections"
        if not base.exists():
            return []
        records = [
            EvidenceRecord.model_validate_json(p.read_text()) for p in base.glob("*/evidence.json")
        ]
        return sorted(records, key=lambda r: r.report.created_at, reverse=True)
