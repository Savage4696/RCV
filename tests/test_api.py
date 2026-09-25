import importlib
import json

import pytest
from fastapi.testclient import TestClient

from receiving_manager.models import Observations
from receiving_manager.scenarios import load_catalog, load_scenarios
from receiving_manager.vision.base import VisionProvider, parse_observations

SCENARIOS = {s.name: s for s in load_scenarios()}
CATALOG_JSON = json.dumps([c.model_dump() for c in load_catalog()])
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("RM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RM_VISION_PROVIDER", "none")
    import receiving_manager.api as module

    module = importlib.reload(module)
    return module, TestClient(module.app)


def photos(n):
    return [("photos", (f"img{i}.png", PNG + bytes([i]), "image/png")) for i in range(n)]


def test_precomputed_inspection_roundtrip(api):
    _, client = api
    s = SCENARIOS["11_spec_example"]
    resp = client.post(
        "/api/inspections",
        data={
            "purchase_order": s.purchase_order.model_dump_json(),
            "catalog": CATALOG_JSON,
            "observations": s.observations.model_dump_json(),
        },
        files=photos(3),
    )
    assert resp.status_code == 200, resp.text
    record = resp.json()
    assert record["report"]["decision"] == "EXCEPTION"
    assert record["observation_source"] == "precomputed"
    iid = record["report"]["inspection_id"]

    assert client.get(f"/api/inspections/{iid}").json()["record_sha256"] == record["record_sha256"]
    assert client.get(f"/api/inspections/{iid}/verify").json()["valid"] is True
    photo = client.get(f"/api/inspections/{iid}/photos/P1")
    assert photo.status_code == 200 and photo.content.startswith(b"\x89PNG")
    assert client.get("/api/inspections").json()[0]["inspection_id"] == iid


def test_tampered_record_fails_verification(api):
    module, client = api
    s = SCENARIOS["01_correct_shipment"]
    record = client.post(
        "/api/inspections",
        data={
            "purchase_order": s.purchase_order.model_dump_json(),
            "catalog": CATALOG_JSON,
            "observations": s.observations.model_dump_json(),
        },
        files=photos(3),
    ).json()
    iid = record["report"]["inspection_id"]
    path = module.settings.data_dir / "inspections" / iid / "evidence.json"
    data = json.loads(path.read_text())
    data["report"]["decision"] = "EXCEPTION"
    path.write_text(json.dumps(data))
    assert client.get(f"/api/inspections/{iid}/verify").json()["valid"] is False


def test_no_provider_and_no_observations_is_uncertain(api):
    _, client = api
    s = SCENARIOS["01_correct_shipment"]
    record = client.post(
        "/api/inspections",
        data={"purchase_order": s.purchase_order.model_dump_json(), "catalog": CATALOG_JSON},
        files=photos(2),
    ).json()
    assert record["report"]["decision"] == "UNCERTAIN"
    assert record["observation_source"] == "none"


def test_vision_provider_is_used(api):
    module, client = api
    s = SCENARIOS["02_short_shipment"]

    class Fake(VisionProvider):
        name = "fake"

        def observe(self, line, catalog_item, images):
            assert [i.photo_id for i in images] == ["P1", "P2", "P3"]
            return s.observations

    module.provider = Fake()
    record = client.post(
        "/api/inspections",
        data={"purchase_order": s.purchase_order.model_dump_json(), "catalog": CATALOG_JSON},
        files=photos(3),
    ).json()
    assert record["observation_source"] == "fake"
    assert record["report"]["decision"] == "EXCEPTION"


def test_rejects_non_images_and_bad_json(api):
    _, client = api
    s = SCENARIOS["01_correct_shipment"]
    r = client.post(
        "/api/inspections",
        data={"purchase_order": s.purchase_order.model_dump_json()},
        files=[("photos", ("a.txt", b"x", "text/plain"))],
    )
    assert r.status_code == 415
    assert client.post("/api/inspections", data={"purchase_order": "{"}).status_code == 422


def test_scenarios_endpoint(api):
    _, client = api
    body = client.get("/api/scenarios").json()
    assert len(body["scenarios"]) >= 10 and body["catalog"]


def test_parse_observations_extracts_json_from_text():
    obs = parse_observations('Here you go:\n```json\n{"damage_coverage": "partial"}\n```')
    assert isinstance(obs, Observations) and obs.damage_coverage == "partial"


def test_benchmark_definitions_and_sample_photos(api):
    _, client = api
    bench = client.get("/api/benchmark").json()
    assert bench["total"] >= 20 and bench["passed"] == bench["total"]
    defs = client.get("/api/definitions").json()
    assert set(defs["verdicts"]) == {"PASS", "FAIL", "UNCERTAIN", "NOT_APPLICABLE"}
    assert set(defs["decisions"]) == {"ACCEPT", "EXCEPTION", "UNCERTAIN"}
    photo = client.get("/api/scenarios/01_correct_shipment/photos/P1")
    assert photo.status_code == 200 and photo.content.startswith(b"\x89PNG")
    assert client.get("/api/scenarios/01_correct_shipment/photos/..%2F..%2Fx").status_code == 404
    assert client.get("/api/budget").json()["session_spent_usd"] == 0


def test_reviewer_failure_is_a_warning_not_a_crash(api):
    module, client = api
    from receiving_manager.reasoning import ReasoningError

    class Broken:
        model = "broken"

        def review(self, *args):
            raise ReasoningError("boom")

    module.reviewer = Broken()
    s = SCENARIOS["01_correct_shipment"]
    record = client.post(
        "/api/inspections",
        data={
            "purchase_order": s.purchase_order.model_dump_json(),
            "catalog": CATALOG_JSON,
            "observations": s.observations.model_dump_json(),
        },
        files=photos(3),
    ).json()
    assert record["report"]["decision"] == "ACCEPT" and record["ai_review"] is None
    assert any("Reasoning review unavailable" in w for w in record["report"]["warnings"])
