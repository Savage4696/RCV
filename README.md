# RCV: AI Receiving Manager

AI-powered inbound inspection. Given a purchase order, a product catalogue and photos of an
incoming shipment, RCV decides whether the shipment contains exactly what was ordered and arrived
in acceptable condition, and seals the result in a tamper-evident evidence record that can back
a supplier claim later.

**UNCERTAIN is a first-class outcome.** RCV never forces a conclusion and never invents
evidence: every finding must cite a submitted photo, meet a confidence threshold and pass
explicit rules, otherwise the check stays UNCERTAIN.

## Decisions and verdicts

Each **check** gets a verdict:

| Verdict | Meaning |
|---|---|
| `PASS` | A cited, usable photo positively proves the expected value, with confidence >= threshold |
| `FAIL` | A cited, usable photo positively proves a discrepancy or defect, with sufficient confidence |
| `UNCERTAIN` | Evidence is missing, partial, low-confidence or ambiguous. Never guessed into PASS/FAIL |
| `NOT_APPLICABLE` | Nothing to check (e.g. the catalogue lists no components) |

The **shipment** gets a decision:

| Decision | Rule | Operator action |
|---|---|---|
| `ACCEPT` | every applicable check PASSED | receive into inventory |
| `EXCEPTION` | at least one check FAILED | hold stock, raise a supplier claim with the evidence record |
| `UNCERTAIN` | no failures, but at least one check UNCERTAIN | re-photograph or inspect manually, then re-run |

The brief's example (PO: 24 x BLUE-BOTTLE-001, blue; observed 22 units, blue, crushed carton)
gives `quantity FAIL`, `variant PASS`, `damage FAIL` -> `EXCEPTION` (scenario `11_spec_example`).

## Checks

| Check | Detects | Key rule |
|---|---|---|
| `sku_identity` | wrong SKU, mixed SKUs, unrecognised identifiers | needs a legible SKU, barcode or ASIN; product name or look-alike is never enough |
| `variant` | wrong colour / variant | observed attributes vs PO line (or catalogue) |
| `quantity` | short / extra units | full visible count, or sealed cartons x pack size once SKU is confirmed; a partial count can prove an over-shipment, never a short one |
| `carton_count` | missing / extra cartons | vs PO or ceil(qty / units per carton) |
| `units_per_carton` | wrong pack size | carton label vs catalogue/PO |
| `damage` | crushing, water, tears, punctures, other | no-damage PASS requires full photo coverage |
| `components` | missing components | missing only when positively seen absent |
| `other_quality` | any other visible quality issue | must cite a photo |

Each check reports expected vs observed, confidence, reason, issue codes (e.g. `SHORT_SHIPMENT`,
`WRONG_VARIANT`, `CARTON_CRUSHED`) and evidence references (photo ID + SHA-256 + what was seen).
Definitions are served at `GET /api/definitions` and shown in the UI ("How decisions work").

## How it works

```
photos ──► vision model (observer only) ──► observations (each cites photo IDs + confidence)
                                                   │
PO + catalogue ────────────────────────────────────┤
                                                   ▼
                    evidence filter: drop anything citing a missing or unusable photo,
                    or below RM_CONFIDENCE_THRESHOLD (default 0.7)
                                                   ▼
                    deterministic rules engine ──► PASS / FAIL / UNCERTAIN per check ──► decision
                                                   ▼
                    reasoning model (default openai/gpt-5-mini via OpenRouter): explains every
                    check, flags concerns, recommends actions, drafts a supplier claim
                                                   ▼
                    sealed evidence record: photo SHA-256s, PO/catalogue snapshot, raw
                    observations, verdicts, AI review, record SHA-256 (data/inspections/<id>/)
```

- The **vision model only reports what it sees**; it never decides pass/fail.
- The **rules engine** (`receiving_manager/engine.py`) makes every decision: transparent, tested.
- The **reasoning model** audits and explains. It cannot change a verdict or upgrade a decision.
  If it explicitly disputes an `ACCEPT`, the shipment is escalated to `UNCERTAIN` (never the
  other way). If it is unavailable, the inspection still completes with a warning.
- The **evidence record** is hashed; `GET /api/inspections/{id}/verify` detects later edits.

## Credit guard

Every paid model call goes through `receiving_manager/llm.py`:

- checks the OpenRouter key's remaining credit (`/auth/key`) and refuses a call that would leave
  less than `RM_MIN_CREDIT_USD` (default $0.25);
- refuses calls once this server process has spent `RM_MAX_SPEND_USD` (default $1.00);
- caps output tokens; charges the estimate when a provider doesn't report cost;
- serves identical requests from an on-disk cache (`data/llm-cache/`) for $0.

A refused call never breaks an inspection: vision failures give UNCERTAIN, a missing review
becomes a warning. Live credit, session spend and cache hits are shown in the UI header and at
`GET /api/budget`. Typical cost: reasoning review ~$0.005, vision on 3 photos ~$0.02.

## Setup

```bash
pip install -e ".[dev]"
cp .env.example .env      # add OPENROUTER_API_KEY (or OPENAI_API_KEY / ANTHROPIC_API_KEY)
set -a; source .env; set +a
```

Without any key the app still runs: submit recorded observations (from a human inspector or the
bundled scenarios). Photo analysis without a model returns `UNCERTAIN`.

| Variable | Default | |
|---|---|---|
| `RM_VISION_PROVIDER` | first key found | `openrouter`, `openai`, `anthropic` or `none` |
| `RM_OPENROUTER_MODEL` | `openai/gpt-4o` | vision model on OpenRouter |
| `RM_REASONING` | `on` | reasoning review (needs `OPENROUTER_API_KEY`) |
| `RM_REASONING_MODEL` / `RM_REASONING_EFFORT` | `openai/gpt-5-mini` / `medium` | |
| `RM_MIN_CREDIT_USD` / `RM_MAX_SPEND_USD` | `0.25` / `1.00` | credit guard |
| `RM_CONFIDENCE_THRESHOLD` | `0.7` | minimum confidence to PASS or FAIL |
| `RM_DATA_DIR` | `data` | evidence records, photos, LLM cache |

## Usage

### Web app

```bash
receiving-manager serve --port 8000   # http://127.0.0.1:8000
```

1. **Inspect**: pick one of 24 sample shipments (filter by expected outcome) or upload your own
   photos, check the PO/catalogue, choose *Recorded observations* (free, deterministic) or
   *AI vision* (uses credit), toggle the reasoning review and run. Results show the decision,
   a card per check (expected vs observed, confidence vs threshold, evidence thumbnails, AI
   explanation), the AI review with recommended actions and a supplier-claim draft, issues,
   warnings, and the evidence record with integrity verification and JSON download.
2. **Scenario benchmark**: runs all scenarios through the rules engine (no cost) and scores them.
3. **How decisions work**: pipeline and definitions of every verdict, decision and check.
4. **History**: every sealed inspection.

### CLI

```bash
receiving-manager inspect --po po.json --catalog catalog.json photo1.jpg photo2.jpg
receiving-manager inspect --po po.json --catalog catalog.json --observations obs.json p1.jpg
receiving-manager inspect ... --no-review --json    # skip AI review, print full record
receiving-manager scenarios                         # benchmark all bundled scenarios
receiving-manager scenarios 11_spec_example         # one scenario
```

### API

| Method | Path | |
|---|---|---|
| POST | `/api/inspections` | multipart: `purchase_order` (JSON), `catalog` (JSON list), `sku`?, `observations`? (JSON), `ai_review`? (default `true`), `photos` (files) |
| GET | `/api/inspections` | list |
| GET | `/api/inspections/{id}` | full evidence record |
| GET | `/api/inspections/{id}/verify` | recompute record hash |
| GET | `/api/inspections/{id}/photos/{photo_id}` | stored photo |
| GET | `/api/scenarios` | bundled scenarios + catalogue |
| GET | `/api/scenarios/{name}/photos/{photo_id}` | synthetic sample photo |
| GET | `/api/benchmark` | score every scenario against its expected outcome (no model cost) |
| GET | `/api/definitions` | verdict / decision / check definitions |
| GET | `/api/budget` | key credit, session spend, cache hits |
| GET | `/api/health` | configured vision and reasoning models |

## Test scenarios

`scenarios/*.json` (24) each hold a PO, recorded observations and the expected decision, check
verdicts and issue codes:

| # | Scenario | Expected |
|---|---|---|
| 01 | Correct shipment | ACCEPT |
| 02 | Short shipment | EXCEPTION |
| 03 | Extra units | EXCEPTION |
| 04 | Wrong SKU | EXCEPTION |
| 05 | Wrong variant (green vs blue) | EXCEPTION |
| 06 | Crushed carton | EXCEPTION |
| 07 | Water-damaged carton | EXCEPTION |
| 08 | Torn packaging | EXCEPTION |
| 09 | Missing components | EXCEPTION |
| 10 | Ambiguous (blurry, dark, distant) | UNCERTAIN |
| 11 | Example from the brief (22 of 24, crushed) | EXCEPTION |
| 12 | Observations citing non-existent photos | UNCERTAIN |
| 13 | Sealed cartons, contents unseen | UNCERTAIN |
| 14 | Mixed SKUs | EXCEPTION |
| 15 | Missing carton | EXCEPTION |
| 16 | Pack-size mismatch | EXCEPTION |
| 17 | Partial photo coverage | UNCERTAIN |
| 18 | Low-confidence possible water mark | UNCERTAIN |
| 19 | Over-shipment proven by a partial count | EXCEPTION |
| 20 | Partial count below expected (can't prove short) | UNCERTAIN |
| 21 | Product name only, no SKU/barcode | UNCERTAIN |
| 22 | Multiple defects | EXCEPTION |
| 23 | Punctured product | EXCEPTION |
| 24 | Complete multi-component lamp kit | ACCEPT |

`scenarios/photos/` holds synthetic sample photos (marked "SYNTHETIC SAMPLE") rendered from the
observations by `scripts/generate_sample_photos.py`; they are loaded in the UI and can be sent
to the vision model.

## Development

```bash
ruff check . && ruff format --check . && pytest
python scripts/generate_sample_photos.py   # regenerate sample photos (needs Pillow, in [dev])
```

Tests cover every scenario, engine edge cases, the API, the credit guard, the cache and the
reasoning-review rules; no test makes a paid call.
