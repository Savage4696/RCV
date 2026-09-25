import argparse
import json
import mimetypes
import sys
from pathlib import Path

import uvicorn
from pydantic import TypeAdapter

from .config import load_settings
from .evidence import EvidenceStore
from .models import AIReview, CatalogItem, InspectionReport, Observations, PurchaseOrder, Verdict
from .scenarios import evaluate, load_catalog, load_scenarios
from .service import UploadedPhoto, run_inspection
from .vision import get_provider, get_reviewer, make_budget, make_cache


def print_report(report: InspectionReport) -> None:
    print(f"PO {report.po_number} / SKU {report.sku}  [inspection {report.inspection_id}]")
    for c in report.checks:
        if c.verdict == Verdict.NOT_APPLICABLE:
            continue
        conf = f" conf={c.confidence:.2f}" if c.confidence is not None else ""
        print(
            f"  {c.check:<17} {c.verdict.value:<9} expected={c.expected!s:<12} "
            f"observed={c.observed!s}{conf}"
        )
        print(f"  {'':<17} {c.reason}")
    print(f"  DECISION: {report.decision.value} - {report.decision_reason}")
    for w in report.warnings:
        print(f"  warning: {w}")


def print_review(review: AIReview) -> None:
    cost = "cached" if review.cached else f"${review.cost_usd:.4f}"
    print(f"  AI REVIEW ({review.model}, {cost}): {review.summary}")
    for c in review.concerns:
        print(f"    concern: {c.description}")
    for a in review.recommended_actions:
        print(f"    action: {a}")
    if review.supplier_claim_draft:
        print(f"    supplier claim draft: {review.supplier_claim_draft}")


def cmd_inspect(args: argparse.Namespace) -> int:
    settings = load_settings()
    po = PurchaseOrder.model_validate_json(Path(args.po).read_text())
    catalog = (
        TypeAdapter(list[CatalogItem]).validate_json(Path(args.catalog).read_text())
        if args.catalog
        else []
    )
    obs = (
        Observations.model_validate_json(Path(args.observations).read_text())
        if args.observations
        else None
    )
    uploads = [
        UploadedPhoto(
            Path(p).name, mimetypes.guess_type(p)[0] or "image/jpeg", Path(p).read_bytes()
        )
        for p in args.photos
    ]
    budget, cache = make_budget(settings), make_cache(settings)
    record = run_inspection(
        po,
        catalog,
        uploads,
        EvidenceStore(settings.data_dir),
        get_provider(settings, budget, cache),
        args.threshold or settings.confidence_threshold,
        sku=args.sku,
        observations=obs,
        reviewer=None if args.no_review else get_reviewer(settings, budget, cache),
    )
    if args.json:
        print(record.model_dump_json(indent=2))
    else:
        print_report(record.report)
        if record.ai_review:
            print_review(record.ai_review)
        print(f"  model cost: ${record.llm_cost_usd:.4f}")
        print(
            f"Evidence record: {settings.data_dir}/inspections/{record.report.inspection_id}/"
            "evidence.json"
        )
    return 0


def cmd_scenarios(args: argparse.Namespace) -> int:
    catalog = load_catalog()
    failures = total = 0
    for s in load_scenarios():
        if args.name and s.name != args.name:
            continue
        total += 1
        report, mismatches = evaluate(s, catalog, args.threshold)
        failures += bool(mismatches)
        print(
            f"=== {s.name}: {s.title}  (expected {s.expected.decision}) "
            f"{'MISMATCH: ' + '; '.join(mismatches) if mismatches else 'OK'}"
        )
        if args.json:
            print(json.dumps(report.model_dump(mode="json"), indent=2))
        else:
            print_report(report)
        print()
    print(f"{total - failures}/{total} scenarios match their expected outcome")
    return 1 if failures else 0


def cmd_serve(args: argparse.Namespace) -> int:
    uvicorn.run("receiving_manager.api:app", host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="receiving-manager")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("inspect", help="Inspect a shipment from photos")
    p.add_argument("--po", required=True, help="Purchase order JSON file")
    p.add_argument("--catalog", help="Catalogue JSON file (list of items)")
    p.add_argument("--sku", help="PO line SKU to inspect (default: first line)")
    p.add_argument("--observations", help="Precomputed observations JSON (skips vision model)")
    p.add_argument("--threshold", type=float, help="Confidence threshold override")
    p.add_argument("--json", action="store_true", help="Print full evidence record as JSON")
    p.add_argument("--no-review", action="store_true", help="Skip the reasoning-model review")
    p.add_argument("photos", nargs="*", help="Photo files")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("scenarios", help="Run the bundled test scenarios")
    p.add_argument("name", nargs="?", help="Run a single scenario")
    p.add_argument("--threshold", type=float, default=0.7)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_scenarios)

    p = sub.add_parser("serve", help="Run the web app")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
