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
                          reasoning model (OpenRouter, default openai/gpt-5-mini): explains every
                          check, flags concerns, recommends actions, drafts a supplier claim.
                          It can only escalate ACCEPT -> UNCERTAIN, never relax a verdict.
                                                   ▼
                          evidence record (photo SHA-256s, PO/catalogue snapshot, raw observations,
                          verdicts, record SHA-256) saved under data/inspections/<id>/
```

The split is deliberate: the model only **reports what it sees**; it never decides pass/fail.
Decisions are made by transparent, testable rules in `receiving_manager/engine.py`.

### Definitions

| Verdict (per check) | Meaning |
|---|---|
| PASS | Photo-cited, confident evidence positively proves the check |
| FAIL | Photo-cited, confident evidence positively proves a discrepancy |
| UNCERTAIN | Evidence is missing, low-confidence, partial or contradictory; never guessed |
| NOT_APPLICABLE | Nothing to check (e.g. no components defined) |

| Decision (shipment) | Meaning |
|---|---|
| ACCEPT | Every applicable check passed |
| EXCEPTION | At least one check failed: raise a supplier claim with the evidence record |
| UNCERTAIN | No failures, but at least one check could not be verified: manual review |

Served at `GET /api/definitions` and shown in the UI ("How decisions work").

### Credit guard

Every model call goes through `receiving_manager/llm.py`: it checks the OpenRouter key's remaining
credit (`/auth/key`) and refuses calls that would drop it below `RM_MIN_CREDIT_USD` or push the
server session past `RM_MAX_SPEND_USD`; output tokens are capped; identical requests are served
from a local cache for $0. Live spend is shown in the UI header and at `GET /api/budget`.

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
cp .env.example .env   # add OPENAI_API_KEY, OPENROUTER_API_KEY or ANTHROPIC_API_KEY
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
| GET | `/api/scenarios/{name}/photos/{photo_id}` | synthetic sample photo |
| GET | `/api/benchmark` | run every scenario through the rules (no model cost) |
| GET | `/api/definitions` | verdict / decision / check definitions |
| GET | `/api/budget` | key credit, session spend, cache hits |

`POST /api/inspections` also accepts `ai_review=false` to skip the reasoning review.

## Test scenarios

`scenarios/*.json` cover: correct shipment, short shipment, extra units, wrong SKU, wrong variant,
crushed carton, water-damaged carton, torn packaging, missing components, ambiguous evidence, the
example from the brief, observations citing non-existent photos, sealed cartons, mixed SKUs, a
missing carton, pack-size mismatch, partial photo coverage, a low-confidence water mark, partial
counts (over and short), product-name-only identity, multiple defects, a punctured product and a
complete multi-component kit (24 total). Each file contains the PO, the observations and the
expected decision/verdicts; the observations stand in for the vision model's output so the rules
are tested deterministically. `scenarios/photos/` holds synthetic sample photos rendered from the
observations by `scripts/generate_sample_photos.py` (usable for live vision runs too).

```bash
pytest && ruff check . && ruff format --check .
```
