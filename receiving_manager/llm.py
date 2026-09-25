"""Shared chat-completions client with a hard credit budget and a response cache.

Every paid model call goes through ``chat_completion``. Before calling, the budget checks the
key's remaining credit (OpenRouter ``/auth/key``) and this process's own spend; if either limit
would be crossed the call is refused. Identical requests are served from the on-disk cache so
re-running a demo costs nothing.
"""

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx


class LLMError(RuntimeError):
    pass


class BudgetExceeded(LLMError):
    pass


@dataclass
class CreditBudget:
    api_key: str | None
    base_url: str
    min_remaining_usd: float = 0.25
    max_spend_usd: float = 1.0
    check_remote: bool = True
    spent_usd: float = 0.0
    calls: int = 0
    cache_hits: int = 0
    _remote: dict | None = None
    _remote_at: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def remote_status(self, max_age: float = 30.0) -> dict | None:
        if not (self.check_remote and self.api_key and "openrouter.ai" in self.base_url):
            return None
        if self._remote is not None and time.monotonic() - self._remote_at < max_age:
            return self._remote
        try:
            resp = httpx.get(
                f"{self.base_url}/auth/key",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=15,
            )
            resp.raise_for_status()
            self._remote = resp.json().get("data") or {}
            self._remote_at = time.monotonic()
        except httpx.HTTPError:
            return self._remote
        return self._remote

    def status(self) -> dict:
        remote = self.remote_status() or {}
        return {
            "limit_usd": remote.get("limit"),
            "remaining_usd": remote.get("limit_remaining"),
            "key_usage_usd": remote.get("usage"),
            "session_spent_usd": round(self.spent_usd, 5),
            "session_cap_usd": self.max_spend_usd,
            "min_remaining_usd": self.min_remaining_usd,
            "calls": self.calls,
            "cache_hits": self.cache_hits,
        }

    def ensure(self, estimate_usd: float) -> None:
        with self._lock:
            self._ensure(estimate_usd)

    def _ensure(self, estimate_usd: float) -> None:
        if self.spent_usd + estimate_usd > self.max_spend_usd:
            raise BudgetExceeded(
                f"Session spend cap reached (${self.spent_usd:.3f} of ${self.max_spend_usd:.2f}); "
                "model call refused"
            )
        remote = self.remote_status(max_age=10.0)
        remaining = remote.get("limit_remaining") if remote else None
        if remaining is not None and remaining - estimate_usd < self.min_remaining_usd:
            raise BudgetExceeded(
                f"Key credit too low (${remaining:.3f} left, "
                f"reserve ${self.min_remaining_usd:.2f}); model call refused"
            )

    def record(self, cost_usd: float) -> None:
        with self._lock:
            self.spent_usd += cost_usd
            self.calls += 1
            if self._remote and self._remote.get("limit_remaining") is not None:
                self._remote["limit_remaining"] -= cost_usd


class ResponseCache:
    def __init__(self, root: Path):
        self.root = root

    def key(self, url: str, body: dict) -> str:
        blob = json.dumps({"url": url, "body": body}, sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()

    def get(self, key: str) -> dict | None:
        path = self.root / f"{key}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def put(self, key: str, value: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / f"{key}.json").write_text(json.dumps(value))


@dataclass
class ChatResult:
    text: str
    cost_usd: float
    cached: bool
    model: str
    reasoning: str | None = None


def chat_completion(
    base_url: str,
    api_key: str,
    body: dict,
    *,
    timeout: float = 120,
    budget: CreditBudget | None = None,
    cache: ResponseCache | None = None,
    estimate_usd: float = 0.05,
) -> ChatResult:
    url = f"{base_url.rstrip('/')}/chat/completions"
    key = cache.key(url, body) if cache else None
    if cache and key and (hit := cache.get(key)):
        if budget:
            budget.cache_hits += 1
        return ChatResult(
            hit["text"], 0.0, True, hit.get("model", body["model"]), hit.get("reasoning")
        )
    if budget:
        budget.ensure(estimate_usd)
    payload = dict(body)
    if "openrouter.ai" in base_url:
        payload["usage"] = {"include": True}
    try:
        resp = httpx.post(
            url, headers={"Authorization": f"Bearer {api_key}"}, json=payload, timeout=timeout
        )
        resp.raise_for_status()
        data = resp.json()
        message = data["choices"][0]["message"]
    except httpx.HTTPStatusError as exc:
        raise LLMError(f"{body['model']} request failed: HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise LLMError(f"{body['model']} request failed: {exc}") from exc
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise LLMError(f"{body['model']} returned an unexpected response") from exc
    usage = data.get("usage") or {}
    cost = float(usage["cost"]) if usage.get("cost") is not None else estimate_usd
    if budget:
        budget.record(cost)
    result = ChatResult(
        text=message.get("content") or "",
        cost_usd=cost,
        cached=False,
        model=data.get("model", body["model"]),
        reasoning=message.get("reasoning"),
    )
    if cache and key and result.text:
        cache.put(key, {"text": result.text, "model": result.model, "reasoning": result.reasoning})
    return result
