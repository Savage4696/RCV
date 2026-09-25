# Receiving Manager

AI-powered inbound inspection. Given a purchase order, product catalogue and photos of an incoming
shipment, it decides whether the shipment matches what was ordered and arrived in acceptable
condition, and produces a tamper-evident evidence record.

**Decisions:** `ACCEPT` (every check passed), `EXCEPTION` (at least one check failed) or
`UNCERTAIN` (no failures, but evidence is insufficient; manual review needed).
**Check verdicts:** `PASS`, `FAIL`, `UNCERTAIN`, `NOT_APPLICABLE`.

## How it works

```
photos ──► vision model (observer only) ──► Observations (every item cites photo IDs + confidence)
                                                   │
PO + catalogue ────────────────────────────────────┤
                                                   ▼
                          evidence sanitiser: drop anything not citing a submitted, usable photo
                                                   ▼
                          deterministic rules engine ──► checks ──► decision
                                                   ▼
                          evidence record (photo SHA-256s, PO/catalogue snapshot, raw observations,
                          verdicts, record SHA-256) saved under data/inspections/<id>/
```

The split is deliberate: the model only **reports what it sees**; it never decides pass/fail.
Decisions are made by transparent, testable rules in `receiving_manager/engine.py`.

### Guarding against forced or invented conclusions

- Observations that cite no photo, an unknown photo, or a photo marked unusable are discarded
  (and a warning is recorded).
- An observation must meet the confidence threshold (`RM_CONFIDENCE_THRESHOLD`, default 0.7) to
  PASS or FAIL a check; otherwise the check is `UNCERTAIN`.
- SKU identity requires a legible SKU, barcode or ASIN. Visual resemblance or product name alone
  is never accepted.
- Quantity is `PASS`/`FAIL` only from a full visible count, or derived from a full count of
  **sealed** cartons x pack size (confidence reduced, flagged as "contents not visually verified").
  A partial count can only prove an over-shipment, never a short one.
- "No damage seen" is a `PASS` only with full photo coverage; with partial coverage it is
  `UNCERTAIN`.
- Components are missing only when positively seen to be absent.

### Checks

| Check | Detects |
|---|---|
| `sku_identity` | wrong SKU, mixed shipment, unrecognised identifiers |
| `variant` | wrong colour / variant |
| `quantity` | short / extra units (expected vs observed) |
| `carton_count` | carton count vs expected (PO or ceil(qty / units per carton)) |
| `units_per_carton` | carton label pack size vs catalogue/PO |
| `damage` | crushing, water damage, tears, punctures, other damage (carton, packaging, product) |
| `components` | missing components (from catalogue) |
| `other_quality` | other visible quality issues |

Each check reports expected, observed, confidence, reason, issue codes and evidence references
(photo ID + SHA-256 + what was seen).

## Setup

```bash
pip install -e ".[dev]"
cp .env.example .env   # add OPENAI_API_KEY or ANTHROPIC_API_KEY
set -a; source .env; set +a
```

Without an API key the app still runs: you can submit recorded observations (e.g. from a human
inspector or the bundled scenarios). Photo analysis without a model returns `UNCERTAIN`.

## Usage

Web app (upload photos, view results and evidence, verify record integrity):

```bash
receiving-manager serve --port 8000   # http://127.0.0.1:8000
```

CLI:

```bash
receiving-manager inspect --po po.json --catalog catalog.json photo1.jpg photo2.jpg
receiving-manager inspect --po po.json --catalog catalog.json --observations obs.json p1.jpg
receiving-manager scenarios            # run all bundled test scenarios
```

API:

| Method | Path | |
|---|---|---|
| POST | `/api/inspections` | multipart: `purchase_order` (JSON), `catalog` (JSON list), `sku`?, `observations`? (JSON), `photos` (files) |
| GET | `/api/inspections` | list |
| GET | `/api/inspections/{id}` | full evidence record |
| GET | `/api/inspections/{id}/verify` | recompute record hash |
| GET | `/api/inspections/{id}/photos/{photo_id}` | stored photo |
| GET | `/api/scenarios` | bundled scenarios + catalogue |

## Test scenarios

`scenarios/*.json` cover: correct shipment, short shipment, extra units, wrong SKU, wrong variant,
crushed carton, water-damaged carton, torn packaging, missing components, ambiguous evidence, the
example from the brief, and observations citing non-existent photos. Each file contains the PO,
the observations and the expected decision/verdicts. Scenario photos are placeholders; the
observations stand in for the vision model's output so the rules can be tested deterministically.

```bash
pytest && ruff check . && ruff format --check .
```
