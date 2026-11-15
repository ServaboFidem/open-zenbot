"""A thin httpx client for the Hindsight memory server.

The only code that speaks Hindsight's HTTP API. Everything is scoped to one
bank under ``/v1/default/banks/{bank}``.
"""

from __future__ import annotations

from typing import Any

import httpx

SOURCE_FACTS_TOKENS = 2048  # source facts of every observation in one recall: half the recall's own 4096


class HindsightError(RuntimeError):
    """Hindsight answered with an error, or could not be reached."""


class HindsightTimeout(HindsightError, TimeoutError):
    """A request to Hindsight timed out."""


class RecallResults(list):
    """A recall's results; `source_facts_truncated` is Hindsight's flag that the source-fact budget cut some."""

    source_facts_truncated = False


class HindsightHTTP:
    def __init__(
        self,
        base_url: str,
        bank: str = "zen",
        *,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.bank = bank
        self._timeout = timeout
        self._client = client or httpx.Client(timeout=timeout)

    @property
    def _bank_path(self) -> str:
        return f"/v1/default/banks/{self.bank}"

    def _send(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        timeout: float | None = None,
        ok_404: bool = False,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        url = f"{self.base_url}{path}"
        try:
            resp = self._client.request(
                method, url, json=json, params=params, timeout=timeout if timeout is not None else self._timeout
            )
        except httpx.TimeoutException as exc:
            raise HindsightTimeout(f"{method} {path} timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise HindsightError(f"{method} {path} failed: {exc}") from exc
        if resp.is_success or (ok_404 and resp.status_code == 404):
            return resp
        raise HindsightError(f"{method} {path} -> {resp.status_code}: {resp.text[:300]}")

    def ready(self) -> bool:
        try:
            return self._send("GET", "/health/ready", timeout=5.0).status_code == 200
        except HindsightError:
            return False

    def ensure_bank(self) -> bool:
        """Make the bank if it is missing. True when this call created it: the records of an earlier bank
        describe documents this one does not hold. (The PUT answers the same either way, so the list asks.)"""
        resp = self._send("GET", "/v1/default/banks", params={"q": self.bank, "limit": 1000})
        banks = (resp.json().get("banks") or []) if resp.content else []
        existed = any(b.get("bank_id") == self.bank for b in banks)
        self._send("PUT", self._bank_path, json={})
        return not existed

    def set_config(self, updates: dict[str, Any]) -> None:
        self._send("PATCH", f"{self._bank_path}/config", json={"updates": updates})

    def retain(
        self,
        doc_id: str,
        text: str,
        tags: list[str],
        *,
        metadata: dict[str, str],
        entities: list[str],
        operation_id: str,
    ) -> str:
        body = {
            "async": True,
            "operation_id": operation_id,
            "items": [
                {
                    "content": text,
                    "document_id": doc_id,
                    "tags": tags,
                    "metadata": metadata,
                    "entities": [{"text": e} for e in entities],
                    "resolve_entities": True,
                    "observation_scopes": "per_tag",
                    "update_mode": "replace",
                }
            ],
        }
        resp = self._send("POST", f"{self._bank_path}/memories", json=body)
        return resp.json().get("operation_id") or operation_id

    def operation(self, op_id: str) -> str:
        resp = self._send("GET", f"{self._bank_path}/operations/{op_id}")
        return resp.json()["status"]

    def cancel(self, op_id: str) -> None:
        self._send("DELETE", f"{self._bank_path}/operations/{op_id}")

    def patch_tags(self, doc_id: str, tags: list[str]) -> None:
        self._send(
            "PATCH",
            f"{self._bank_path}/documents/{doc_id}",
            json={"tags": tags, "observation_scopes": "per_tag"},
        )

    def delete_document(self, doc_id: str) -> bool:
        resp = self._send("DELETE", f"{self._bank_path}/documents/{doc_id}", ok_404=True)
        return resp.status_code != 404

    def recall(
        self,
        query: str,
        tags: list[str],
        *,
        budget: str = "mid",
        max_tokens: int = 4096,
        timeout: float | None = None,
    ) -> RecallResults:
        body = {
            "query": query,
            "tags": tags,
            "tags_match": "any_strict",
            "prefer_observations": True,
            "budget": budget,
            "max_tokens": max_tokens,
            "include": {"source_facts": {"max_tokens": SOURCE_FACTS_TOKENS}},
        }
        resp = self._send("POST", f"{self._bank_path}/memories/recall", json=body, timeout=timeout)
        data = resp.json()
        results = RecallResults(data.get("results", []))
        results.source_facts_truncated = bool(data.get("source_facts_truncated"))
        sources = data.get("source_facts") or {}
        for r in results:  # each observation carries the facts behind it; an id cut by the token budget is skipped
            if ids := r.get("source_fact_ids"):
                r["source_facts"] = [sources[i] for i in ids if i in sources]
        return results
