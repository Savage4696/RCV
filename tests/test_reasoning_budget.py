import httpx
import pytest

from receiving_manager import llm
from receiving_manager.engine import inspect
from receiving_manager.llm import BudgetExceeded, CreditBudget, ResponseCache, chat_completion
from receiving_manager.models import AIReview, Decision, ReviewConcern
from receiving_manager.reasoning import ReasoningReviewer, apply_review
from receiving_manager.scenarios import load_catalog, load_scenarios, placeholder_photos

SCENARIOS = {s.name: s for s in load_scenarios()}
CATALOG = load_catalog()


def report_for(name):
    s = SCENARIOS[name]
    report, clean = inspect(s.purchase_order, CATALOG, placeholder_photos(s), s.observations)
    return s, report, clean


class FakeResponse:
    def __init__(self, content, cost=0.004):
        self._data = {
            "model": "openai/gpt-5-mini",
            "choices": [{"message": {"content": content}}],
            "usage": {"cost": cost},
        }

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def test_session_cap_refuses_call(monkeypatch):
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: pytest.fail("must not call"))
    budget = CreditBudget(api_key=None, base_url="x", max_spend_usd=0.01, spent_usd=0.009)
    with pytest.raises(BudgetExceeded):
        chat_completion("https://api.openai.com/v1", "k", {"model": "m"}, budget=budget)


def test_low_remote_credit_refuses_call(monkeypatch):
    budget = CreditBudget(api_key="k", base_url="https://openrouter.ai/api/v1")
    monkeypatch.setattr(budget, "remote_status", lambda max_age=30: {"limit_remaining": 0.2})
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: pytest.fail("must not call"))
    with pytest.raises(BudgetExceeded):
        chat_completion("https://openrouter.ai/api/v1", "k", {"model": "m"}, budget=budget)


def test_cache_serves_repeat_calls_for_free(monkeypatch, tmp_path):
    calls = []

    def post(*args, **kwargs):
        calls.append(kwargs["json"])
        return FakeResponse('{"a": 1}')

    monkeypatch.setattr(llm.httpx, "post", post)
    budget = CreditBudget(api_key=None, base_url="x")
    cache = ResponseCache(tmp_path)
    first = chat_completion("https://api.x/v1", "k", {"model": "m"}, budget=budget, cache=cache)
    second = chat_completion("https://api.x/v1", "k", {"model": "m"}, budget=budget, cache=cache)
    assert len(calls) == 1 and not first.cached and second.cached and second.cost_usd == 0
    assert budget.spent_usd == pytest.approx(0.004) and budget.cache_hits == 1


def test_reviewer_parses_review_and_filters_unknown_photos(monkeypatch):
    content = (
        '{"summary": "Short by 2 units and a crushed carton.", '
        '"check_explanations": [{"check": "quantity", "explanation": "22 of 24 in P3"}], '
        '"concerns": [{"description": "x", "photo_ids": ["P3", "P9"]}], '
        '"recommended_actions": ["Quarantine carton 1"], '
        '"supplier_claim_draft": "Claim...", "agrees_with_decision": true}'
    )
    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResponse(content))
    s, report, clean = report_for("11_spec_example")
    review = ReasoningReviewer("k", "openai/gpt-5-mini").review(
        report, s.purchase_order.lines[0], None, clean, ["P1", "P2", "P3"]
    )
    assert review.check_explanations[0].check == "quantity"
    assert review.concerns[0].photo_ids == ["P3"]
    assert review.cost_usd == pytest.approx(0.004)


def test_review_can_only_escalate_accept():
    _, report, _ = report_for("01_correct_shipment")
    assert report.decision == Decision.ACCEPT
    note = AIReview(model="m", summary="s", concerns=[ReviewConcern(description="minor note")])
    apply_review(report, note)
    assert report.decision == Decision.ACCEPT and not note.escalated
    review = AIReview(
        model="m",
        summary="s",
        concerns=[ReviewConcern(description="thin")],
        agrees_with_decision=False,
    )
    apply_review(report, review)
    assert report.decision == Decision.UNCERTAIN and review.escalated
    assert "thin" in report.decision_reason

    _, report, _ = report_for("11_spec_example")
    review = AIReview(model="m", summary="s", agrees_with_decision=False)
    apply_review(report, review)
    assert report.decision == Decision.EXCEPTION and not review.escalated

    _, report, _ = report_for("10_ambiguous")
    apply_review(report, AIReview(model="m", summary="looks fine", agrees_with_decision=True))
    assert report.decision == Decision.UNCERTAIN


def test_missing_usage_cost_is_charged_at_estimate(monkeypatch):
    response = FakeResponse('{"a": 1}')
    response._data.pop("usage")
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: response)
    budget = CreditBudget(api_key=None, base_url="x")
    chat_completion("https://api.x/v1", "k", {"model": "m"}, budget=budget, estimate_usd=0.03)
    assert budget.spent_usd == pytest.approx(0.03)
