import json

import httpx
import pytest

from src.memory.hindsight_http import HindsightError, HindsightHTTP, HindsightTimeout

BANK = "/v1/default/banks/zen"


def make(handler):
    seen = []

    def wrapped(request):
        seen.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(wrapped), base_url="http://hs")
    return HindsightHTTP("http://hs", client=client), seen


def test_retain_sends_one_async_per_tag_replace_item():
    http, seen = make(lambda r: httpx.Response(200, json={"success": True, "operation_id": "op-1"}))
    op = http.retain(
        "doc-1", "hello", ["t1", "t2"],
        metadata={"k": "v"}, entities=["Dana Lee"], operation_id="op-1",
    )
    assert op == "op-1"
    req = seen[0]
    assert req.method == "POST" and req.url.path == f"{BANK}/memories"
    body = json.loads(req.content)
    assert body["async"] is True
    assert body["operation_id"] == "op-1"
    (item,) = body["items"]
    assert item["document_id"] == "doc-1"
    assert item["content"] == "hello"
    assert item["tags"] == ["t1", "t2"]
    assert item["metadata"] == {"k": "v"}
    assert item["entities"] == [{"text": "Dana Lee"}]
    assert item["resolve_entities"] is True
    assert item["observation_scopes"] == "per_tag"
    assert item["update_mode"] == "replace"


def test_recall_is_strict_by_tag_and_prefers_observations():
    http, seen = make(lambda r: httpx.Response(200, json={"results": [{"id": "1", "text": "x"}]}))
    out = http.recall("who?", ["t1"])
    assert out == [{"id": "1", "text": "x"}]
    assert seen[0].url.path == f"{BANK}/memories/recall"
    body = json.loads(seen[0].content)
    assert body["tags"] == ["t1"]
    assert body["tags_match"] == "any_strict"
    assert body["prefer_observations"] is True
    assert body["budget"] == "mid"
    assert body["max_tokens"] == 4096
    assert body["include"] == {"source_facts": {"max_tokens": 2048}}


def test_recall_hands_each_observation_its_source_facts():
    sources = {"f1": {"id": "f1", "text": "a", "document_id": "d1", "tags": ["t1"]},
               "f2": {"id": "f2", "text": "b", "document_id": "d2", "tags": ["t1"]}}
    results = [{"id": "o1", "type": "observation", "text": "ab", "source_fact_ids": ["f2", "gone", "f1"]},
               {"id": "w1", "type": "world", "text": "c", "document_id": "d3"}]
    http, _ = make(lambda r: httpx.Response(200, json={"results": results, "source_facts": sources,
                                                       "source_facts_truncated": True}))
    obs, world = http.recall("q", ["t1"])
    assert [f["id"] for f in obs["source_facts"]] == ["f2", "f1"]  # truncated: the missing id is skipped
    assert "source_facts" not in world


def test_patch_tags_replaces_the_set():
    http, seen = make(lambda r: httpx.Response(200, json={}))
    http.patch_tags("doc-1", ["a"])
    assert seen[0].method == "PATCH"
    assert seen[0].url.path == f"{BANK}/documents/doc-1"
    assert json.loads(seen[0].content) == {"tags": ["a"], "observation_scopes": "per_tag"}


def test_delete_missing_document_is_false():
    http, _ = make(lambda r: httpx.Response(404, json={"detail": "no"}))
    assert http.delete_document("nope") is False


def test_delete_document_true_on_success():
    http, seen = make(lambda r: httpx.Response(200, json={}))
    assert http.delete_document("d") is True
    assert seen[0].method == "DELETE"


def test_lifecycle_calls():
    def handler(r):
        if r.url.path == "/health/ready":
            return httpx.Response(200)
        if r.url.path.endswith("/operations/op-1") and r.method == "GET":
            return httpx.Response(200, json={"operation_id": "op-1", "status": "processing"})
        return httpx.Response(200, json={})

    http, seen = make(handler)
    assert http.ready() is True
    http.ensure_bank()
    http.set_config({"a": 1})
    assert http.operation("op-1") == "processing"
    http.cancel("op-1")
    by = {(r.method, r.url.path): r for r in seen}
    assert json.loads(by[("PUT", BANK)].content) == {}
    assert json.loads(by[("PATCH", f"{BANK}/config")].content) == {"updates": {"a": 1}}
    assert ("DELETE", f"{BANK}/operations/op-1") in by


def test_ready_false_on_error():
    def boom(r):
        raise httpx.ConnectError("down")

    http, _ = make(boom)
    assert http.ready() is False


def test_errors_carry_status():
    http, _ = make(lambda r: httpx.Response(500, text="kaboom"))
    with pytest.raises(HindsightError, match="500"):
        http.ensure_bank()


def test_connection_error_is_hindsight_error():
    def boom(r):
        raise httpx.ConnectError("down")

    http, _ = make(boom)
    with pytest.raises(HindsightError):
        http.ensure_bank()


def test_timeout_is_hindsight_timeout():
    def slow(r):
        raise httpx.ReadTimeout("slow")

    http, _ = make(slow)
    with pytest.raises(HindsightTimeout) as ei:
        http.recall("q", [])
    assert isinstance(ei.value, TimeoutError)


def test_ensure_bank_says_whether_it_created_the_bank():
    banks = []

    def handler(r):
        if r.method == "GET" and r.url.path == "/v1/default/banks":
            assert r.url.params["q"] == "zen"
            return httpx.Response(200, json={"banks": [{"bank_id": b} for b in banks]})
        if r.method == "PUT":
            banks.append("zen")
        return httpx.Response(200, json={})

    http, _ = make(handler)
    banks.append("zen-other")  # a substring match is not the bank
    assert http.ensure_bank() is True
    assert http.ensure_bank() is False


def test_recall_reports_truncated_source_facts():
    http, _ = make(lambda r: httpx.Response(200, json={"results": [], "source_facts_truncated": True}))
    assert http.recall("q", ["t"]).source_facts_truncated is True
    http, _ = make(lambda r: httpx.Response(200, json={"results": []}))
    assert http.recall("q", ["t"]).source_facts_truncated is False
