import copy
import json

import httpx
import pytest

from src.skilltuner.casemark import ANSWER_LIMIT, GAP_S, MCP_URL, CaseMark, SourceError
from tests.skilltuner.conftest import fixture, rpc


async def kind_of(coro) -> str:
    with pytest.raises(SourceError) as info:
        await coro
    return info.value.kind


async def test_search_returns_the_first_five_and_asks_only_the_endpoint(fake_casemark):
    found = await fake_casemark.reader().search("insurance financials")
    assert len(found) == 5
    assert found[0].slug == "analyzing-insurance-financials" and found[0].tags == ["analysis", "insurance"]
    req = fake_casemark.requests[0]
    assert str(req.url) == MCP_URL and req.method == "POST"
    body = json.loads(req.content)
    assert body["jsonrpc"] == "2.0" and body["method"] == "tools/call"
    assert body["params"] == {"name": "resolve_skill", "arguments": {"query": "insurance financials"}}
    assert "text/event-stream" in req.headers["accept"]


async def test_search_skips_results_with_no_slug_or_a_bad_one(fake_casemark):
    rows = fake_casemark.search_answer["results"]
    fake_casemark.search_answer = {"results": [{"name": "x"}, {**rows[0], "slug": "Bad/Slug"}, {**rows[1], "slug": 5}, rows[2]]}
    found = await fake_casemark.reader().search("q")
    assert [f.slug for f in found] == [rows[2]["slug"]]


async def test_read_parses_the_skill_and_keeps_only_reference_text_files(fake_casemark):
    answer = copy.deepcopy(fake_casemark.read_answer)
    answer["bundle"]["files"] += [
        {"slug": "a--file--scripts/x.md", "path": "scripts/x.md", "content_type": "text/markdown"},
        {"slug": "a--file--references/x.png", "path": "references/x.png", "content_type": "text/plain"},
        {"slug": "a--file--references/y.md", "path": "references/y.md", "content_type": "image/png"},
        {"slug": "a--file--references/../z.md", "path": "references/../z.md", "content_type": "text/markdown"},
    ]
    fake_casemark.read_answer = answer
    got = await fake_casemark.reader().read("drafting-investment-memos")
    assert got.slug == "drafting-investment-memos" and got.license == "Apache-2.0" and got.author == "casemark"
    assert got.version == "4" and got.body.startswith(answer["content"][:20]) and got.tags
    assert got.files == [("references/MEMO-TEMPLATE.md", "drafting-investment-memos--file--references/MEMO-TEMPLATE.md")]
    body = json.loads(fake_casemark.requests[0].content)
    assert body["params"] == {"name": "read_skill", "arguments": {"slug": "drafting-investment-memos"}}


async def test_read_refuses_a_bad_slug_before_any_request(fake_casemark):
    for bad in ("../x", "A", "", "a" * 101, "a--file--references/x.md"):
        with pytest.raises(ValueError):
            await fake_casemark.reader().read(bad)
    assert fake_casemark.requests == []


async def test_read_file_answers_the_content(fake_casemark):
    text = await fake_casemark.reader().read_file("drafting-investment-memos", "references/MEMO-TEMPLATE.md")
    assert text == fake_casemark.file_answer["content"]
    args = json.loads(fake_casemark.requests[0].content)["params"]["arguments"]
    assert args == {"slug": "drafting-investment-memos--file--references/MEMO-TEMPLATE.md"}


async def test_read_file_refuses_a_bad_path_or_root(fake_casemark):
    for root, path in (("ok", "references/../x.md"), ("ok", "scripts/x.md"), ("ok", "references/x.py"), ("Bad", "references/x.md")):
        with pytest.raises(ValueError):
            await fake_casemark.reader().read_file(root, path)
    assert fake_casemark.requests == []


async def test_unknown_slug_is_not_found(fake_casemark):
    fake_casemark.read_answer = {"error": True, "url": "u", "statusCode": 404, "statusMessage": "Not Found", "message": "m"}
    assert await kind_of(fake_casemark.reader().read("nope")) == "not_found"


async def test_other_inner_error_is_provider(fake_casemark):
    fake_casemark.read_answer = {"error": True, "statusCode": 500, "message": "m"}
    assert await kind_of(fake_casemark.reader().read("nope")) == "provider"


async def test_jsonrpc_error_is_provider(fake_casemark):
    fake_casemark.raw = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "x"}}).encode()
    assert await kind_of(fake_casemark.reader().search("q")) == "provider"


async def test_status_mapping(fake_casemark):
    fake_casemark.raw = b"{}"
    fake_casemark.status = 429
    assert await kind_of(fake_casemark.reader().search("q")) == "refused"
    fake_casemark.status = 503
    assert await kind_of(fake_casemark.reader().search("q")) == "provider"


async def test_a_redirect_is_source_changed_and_not_followed(fake_casemark):
    fake_casemark.raw = b""
    fake_casemark.status = 302
    fake_casemark.headers = {"location": "https://evil.example/"}
    assert await kind_of(fake_casemark.reader().search("q")) == "source_changed"
    assert len(fake_casemark.requests) == 1


async def test_wrong_shapes_are_source_changed(fake_casemark):
    for raw in (b"not json", b"[]", json.dumps({"result": {}}).encode(),
                json.dumps(rpc("not json inside")).encode(), json.dumps(rpc([1])).encode(),
                json.dumps({"result": {"content": []}}).encode()):
        fake_casemark.raw = raw
        assert await kind_of(fake_casemark.reader().read("drafting-investment-memos")) == "source_changed", raw
    fake_casemark.raw = json.dumps(rpc(fixture("search"))).encode()  # a search answer to a read
    assert await kind_of(fake_casemark.reader().read("drafting-investment-memos")) == "source_changed"


async def test_another_content_type_is_source_changed(fake_casemark):
    fake_casemark.raw = json.dumps(rpc(fixture("read"))).encode()
    fake_casemark.headers = {"content-type": "text/event-stream"}
    assert await kind_of(fake_casemark.reader().read("drafting-investment-memos")) == "source_changed"


async def test_an_answer_over_the_limit_is_provider(fake_casemark):
    fake_casemark.raw = b" " * (ANSWER_LIMIT + 1)
    assert await kind_of(fake_casemark.reader().search("q")) == "provider"


async def test_a_connect_error_is_network(fake_casemark):
    fake_casemark.raises = httpx.ConnectError("boom")
    assert await kind_of(fake_casemark.reader().search("q")) == "network"


async def test_requests_are_a_gap_apart_and_the_first_is_not_delayed(fake_casemark):
    reader = fake_casemark.reader()
    await reader.search("q")
    assert fake_casemark.sleeps == []
    await reader.read("drafting-investment-memos")
    await reader.read_file("a", "references/x.md")
    assert fake_casemark.sleeps == [GAP_S, GAP_S]


async def test_on_send_false_is_budget_and_sends_nothing(fake_casemark):
    fake_casemark.allow = False
    assert await kind_of(fake_casemark.reader().search("q")) == "budget"
    assert fake_casemark.requests == []


async def test_on_send_is_called_before_each_request(fake_casemark):
    calls = []
    reader = CaseMark(lambda: fake_casemark.client, sleep=fake_casemark.sleep,
                      on_send=lambda: calls.append(len(fake_casemark.requests)) or True)
    await reader.search("q")
    await reader.read("drafting-investment-memos")
    assert calls == [0, 1]


async def test_text_is_cleaned_and_capped(fake_casemark):
    row = {**fake_casemark.search_answer["results"][0], "summary": "a\x00b c d\x7fe\nf\tg", "name": "n" * 500}
    fake_casemark.search_answer = {"results": [row]}
    found = (await fake_casemark.reader().search("q"))[0]
    assert found.summary == "abcde\nf\tg" and len(found.name) == 120
    fake_casemark.read_answer = {**fake_casemark.read_answer, "content": "x" * 300_000, "summary": "s" * 2000}
    got = await fake_casemark.reader().read("drafting-investment-memos")
    assert len(got.body) == 200_000 and len(got.summary) == 1000
