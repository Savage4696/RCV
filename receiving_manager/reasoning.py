"""Reasoning-model reviewer.

A reasoning model audits the deterministic report and writes a plain-language explanation for
the operator, recommended actions and (for exceptions) a supplier-claim draft. It is strictly
subordinate to the rules engine: it cannot change a check verdict and cannot upgrade a decision.
If it disagrees with an ACCEPT, the decision is escalated to UNCERTAIN for human review.
"""

import json
import re

from pydantic import ValidationError

from .llm import CreditBudget, LLMError, ResponseCache, chat_completion
from .models import AIReview, CatalogItem, Decision, InspectionReport, Observations, POLine

SYSTEM_PROMPT = """You are a senior receiving-quality auditor reviewing an automated inspection.
A deterministic rules engine has already produced check verdicts from photo-cited observations.
Your job is to explain and audit, not to re-decide.

Rules:
- Use ONLY the facts in the input. Never invent counts, identifiers, damage or photos.
- Cite photo IDs (P1, P2, ...) exactly as given when you refer to evidence.
- Explain every check that is not NOT_APPLICABLE in one or two plain sentences an operator
  understands: what was expected, what the photos showed, and why that gives PASS/FAIL/UNCERTAIN.
- UNCERTAIN is a correct, valid outcome when evidence is insufficient. Do not push it to PASS.
- Raise a concern only for a concrete problem in the input: observations that contradict each
  other, notes that mention something the checks ignored, or a PASS resting on thin evidence.
  Set agrees_with_decision=false only if such a concern means the decision is not supported.
- recommended_actions: short, concrete next steps for the receiving operator (e.g. "Photograph
  the label on carton 2", "Open carton 1 and count units", "Quarantine the damaged carton").
- supplier_claim_draft: only for EXCEPTION, a short factual claim to the supplier listing each
  discrepancy with its photo IDs; otherwise null.

Respond with one JSON object only:
{"summary": str, "check_explanations": [{"check": str, "explanation": str}],
 "concerns": [{"description": str, "checks": [str], "photo_ids": [str]}],
 "recommended_actions": [str], "supplier_claim_draft": str | null,
 "agrees_with_decision": bool}"""


class ReasoningError(RuntimeError):
    pass


def _payload(
    report: InspectionReport, line: POLine, item: CatalogItem | None, obs: Observations
) -> dict:
    return {
        "purchase_order_line": line.model_dump(),
        "catalog_item": item.model_dump() if item else None,
        "observations_after_evidence_filtering": obs.model_dump(exclude_defaults=True),
        "confidence_threshold": report.confidence_threshold,
        "checks": [
            c.model_dump(exclude={"issues"}, exclude_none=True, mode="json") for c in report.checks
        ],
        "decision": report.decision.value,
        "decision_reason": report.decision_reason,
        "warnings": report.warnings,
    }


class ReasoningReviewer:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://openrouter.ai/api/v1",
        effort: str = "medium",
        budget: CreditBudget | None = None,
        cache: ResponseCache | None = None,
        max_tokens: int = 6000,
        timeout: float = 180,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.effort = effort
        self.budget = budget
        self.cache = cache
        self.max_tokens = max_tokens
        self.timeout = timeout

    def review(
        self,
        report: InspectionReport,
        line: POLine,
        item: CatalogItem | None,
        obs: Observations,
        photo_ids: list[str],
    ) -> AIReview:
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "reasoning": {"effort": self.effort, "exclude": True},
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(_payload(report, line, item, obs))},
            ],
        }
        try:
            result = chat_completion(
                self.base_url,
                self.api_key,
                body,
                timeout=self.timeout,
                budget=self.budget,
                cache=self.cache,
                estimate_usd=0.02,
            )
        except LLMError as exc:
            raise ReasoningError(str(exc)) from exc
        match = re.search(r"\{.*\}", result.text, re.DOTALL)
        if not match:
            raise ReasoningError("Reasoning model did not return JSON")
        try:
            review = AIReview.model_validate(
                {
                    **json.loads(match.group(0)),
                    "model": result.model,
                    "cost_usd": result.cost_usd,
                    "cached": result.cached,
                }
            )
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ReasoningError(f"Reasoning model returned an invalid review: {exc}") from exc
        for concern in review.concerns:
            concern.photo_ids = [p for p in concern.photo_ids if p in photo_ids]
        return review


def apply_review(report: InspectionReport, review: AIReview) -> None:
    """The review can only escalate ACCEPT to UNCERTAIN, never relax a verdict."""
    if report.decision == Decision.ACCEPT and (not review.agrees_with_decision or review.concerns):
        review.escalated = True
        concerns = "; ".join(c.description for c in review.concerns)
        report.decision = Decision.UNCERTAIN
        report.decision_reason = (
            "All rule checks passed, but the reasoning reviewer raised concerns: "
            f"{concerns or 'reviewer does not agree with ACCEPT'}. Manual review required"
        )
