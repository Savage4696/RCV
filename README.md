# RCV — AI Receiving Manager

> **AI-powered inbound inspection for real-world receiving operations.**

Given a purchase order, product catalogue, and shipment photos, RCV determines whether an incoming shipment matches what was ordered and arrived in acceptable condition — then seals the result into a **tamper-evident evidence record**.

### The core idea

**AI observes. Evidence supports. Rules decide.**

RCV deliberately separates perception from decision-making:

```
Shipment Photos
      ↓
Vision Model — observes only
      ↓
Evidence + confidence
      ↓
Deterministic Rules Engine
      ↓
PASS / FAIL / UNCERTAIN
      ↓
Shipment Decision
      ↓
Sealed Evidence Record
      ↓
AI Review + Recommended Action
```

**UNCERTAIN is a first-class outcome.** RCV never guesses a PASS/FAIL when evidence is missing, ambiguous, low-confidence, or insufficient.

---

## Why this is interesting

Receiving operations sit at the intersection of **physical inventory, supplier claims, human inspection, and incomplete visual evidence**.

RCV is designed around a simple principle:

> **AI should make inspection faster without making the system less accountable.**

Every finding can point back to submitted evidence. Decisions are made by explicit rules. The final inspection record is hashed so later edits can be detected.

---

## Decisions

| Verdict | Meaning |
|---|---|
| `PASS` | Evidence proves the expected value |
| `FAIL` | Evidence proves a discrepancy or defect |
| `UNCERTAIN` | Evidence is missing, partial, ambiguous, or below threshold |
| `NOT_APPLICABLE` | There is nothing applicable to check |

Shipment-level decisions:

| Decision | Meaning |
|---|---|
| `ACCEPT` | All applicable checks pass |
| `EXCEPTION` | At least one check fails |
| `UNCERTAIN` | No failure, but evidence is insufficient |

---

## What RCV checks

- SKU identity
- Product variant
- Quantity
- Carton count
- Units per carton
- Packaging damage
- Components
- Other visible quality issues

Each check records **expected vs observed, confidence, reason, issue codes, and evidence references**.

---

## Engineering

### Vision is not the decision-maker

The vision model only produces observations.

### Rules are deterministic

`receiving_manager/engine.py` evaluates evidence against explicit business rules and produces the verdict.

### Reasoning audits the result

The reasoning model explains the inspection, highlights concerns, recommends actions, and can escalate an `ACCEPT` to `UNCERTAIN` — but cannot upgrade an uncertain or failed decision.

### Evidence is verifiable

Inspection records contain photo hashes, the PO/catalogue snapshot, observations, verdicts, and the final record hash.

`GET /api/inspections/{id}/verify` recomputes the hash to detect later changes.

---

## Built-in benchmark

RCV includes **24 inspection scenarios** covering:

- Correct shipments
- Short / extra shipments
- Wrong SKUs and variants
- Damaged packaging
- Missing components
- Ambiguous evidence
- Partial photo coverage
- Low-confidence observations
- Mixed SKUs
- Pack-size mismatches
- Multiple defects

The benchmark runs through the deterministic rules engine without paid model calls.

---

## Cost & reliability controls

Every paid model call passes through a credit guard.

- Minimum remaining credit threshold
- Per-session spend cap
- Output-token limits
- On-disk request caching
- Graceful degradation when models are unavailable

Typical documented costs are approximately **$0.005 for reasoning** and **$0.02 for vision on three photos**.

---

## Quick start

```bash
pip install -e ".[dev]"
cp .env.example .env
set -a; source .env; set +a

receiving-manager serve --port 8000
```

The application can also run without model credentials using recorded observations and bundled scenarios.

---

## CLI

```bash
receiving-manager inspect --po po.json --catalog catalog.json photo1.jpg photo2.jpg
receiving-manager scenarios
receiving-manager scenarios 11_spec_example
```

---

## API

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/inspections` | Run an inspection |
| GET | `/api/inspections` | List inspections |
| GET | `/api/inspections/{id}` | View evidence record |
| GET | `/api/inspections/{id}/verify` | Verify record integrity |
| GET | `/api/benchmark` | Run scenario benchmark |
| GET | `/api/definitions` | View decision definitions |
| GET | `/api/budget` | View model budget state |
| GET | `/api/health` | View model configuration |

---

## Testing

```bash
ruff check .
ruff format --check .
pytest
```

Tests cover scenarios, rules-engine edge cases, the API, credit guard, caching, and reasoning-review rules.

---

### Built to make AI useful in workflows where **evidence, accountability, and deterministic decisions matter.**
