"""work docs: a doc is converted to Markdown (plain read, or the
markdownify MCP by suffix) under the vault, sandboxed by
MD_ALLOWED_PATHS; one function owns the vault layout; the sync log keeps
each file's md5; memory gets a short summary plus names, dates, facts,
and a vault pointer, never full text; an md5 change re-summarizes and
updates memory on its own; every write passes the exclusion filter."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.generation.generator import Generator
from src.memory.exclusion import exclusion
from src.memory.graph import LocalGraph, MemoryGraph, SHARED
from src.memory.ingest import (IngestError, Ingestor, MarkdownifyConverter, MarkitdownConverter, PlainConverter, CompositeConverter,
                               VAULT_SCHEMA, vault_path_for)
from src.memory.ledger import Ledger
from src.memory.sync_log import SyncLog

from .conftest import ScriptedProvider, completion

SUMMARY = completion('{"summary": "Acme contract terms.", "names": ["Acme", "Dana"], "dates": ["2026-12-01"], "facts": ["Net 30 payment."]}')


def make(vault: Path, ledger: Ledger, provider=None, converter=None, allowed=None):
    provider = provider or ScriptedProvider([SUMMARY])
    graph = MemoryGraph(LocalGraph(vault / "memory"), ledger)
    ing = Ingestor(vault, converter or PlainConverter(), Generator(provider), graph, ledger, allowed=allowed or [vault / "uploads"])
    return ing, graph, provider


def upload(vault: Path, name: str, text: str) -> Path:
    p = vault / "uploads" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# -- layout and sandbox ------------------------------------------------------


def test_one_function_owns_the_vault_layout(vault: Path):
    assert VAULT_SCHEMA == 1
    assert vault_path_for(vault, Path("C:/anywhere/Acme Contract v2.PDF")) == vault / "docs" / "acme-contract-v2.md"
    assert vault_path_for(vault, Path("/x/notes.md")) == vault / "docs" / "notes.md"


async def test_paths_outside_md_allowed_paths_are_refused(vault: Path, ledger: Ledger, tmp_path: Path):
    ing, graph, provider = make(vault, ledger)
    outside = tmp_path / "elsewhere.md"
    outside.write_text("# secret plans", encoding="utf-8")
    with pytest.raises(IngestError, match="MD_ALLOWED_PATHS"):
        await ing.ingest(outside)
    assert provider.calls == [] and not (vault / "docs").exists()
    assert ledger.tail(kind="ingest")[-1].data["status"] == "refused"


# -- ingest ------------------------------------------------------------------


async def test_ingest_writes_vault_doc_sync_log_and_a_memory_pointer(vault: Path, ledger: Ledger):
    ing, graph, provider = make(vault, ledger)
    src = upload(vault, "acme-contract.md", "# Acme contract\n\nDana signed. Net 30. Renewal 2026-12-01.\n")
    res = await ing.ingest(src, bot_id="user")
    assert res.status == "new" and res.vault_path == vault / "docs" / "acme-contract.md"
    assert res.vault_path.read_text(encoding="utf-8").startswith("# Acme contract")
    assert res.md5 == hashlib.md5(src.read_bytes()).hexdigest()
    entry = ing.sync_log.get(str(src))
    assert entry and entry.md5 == res.md5 and entry.fact_id == res.fact_id
    fact = graph.get(res.fact_id)
    assert fact.namespace == SHARED and str(fact.tier) == "project"
    assert "Acme contract terms." in fact.text and "Net 30 payment." in fact.text and "2026-12-01" in fact.text
    assert fact.source == "vault:docs/acme-contract.md" and "vault:docs/acme-contract.md" in fact.text
    assert "Dana signed" not in fact.text  # never the full text
    assert fact.entities == ["acme", "dana"]
    assert provider.calls[0][1].content.startswith("Document title: acme-contract")
    e = ledger.tail(kind="ingest")[-1].data
    assert e["status"] == "new" and e["md5"] == res.md5 and e["fact_id"] == res.fact_id and e["vault_path"] == "docs/acme-contract.md"


async def test_unchanged_doc_is_skipped_without_a_model_call(vault: Path, ledger: Ledger):
    ing, graph, provider = make(vault, ledger, ScriptedProvider([SUMMARY]))
    src = upload(vault, "a.md", "# A\n\nbody\n")
    first = await ing.ingest(src)
    again = await ing.ingest(src)
    assert again.status == "unchanged" and again.fact_id == first.fact_id and len(provider.calls) == 1
    assert len(graph.facts(SHARED)) == 1


async def test_a_logged_doc_whose_fact_is_not_in_the_store_is_imported_again(vault: Path, ledger: Ledger):
    # An upgrade from cognee keeps <vault>/sync.json: every file looks unchanged, with a fact the new store lacks.
    ing, graph, provider = make(vault, ledger, ScriptedProvider([SUMMARY, SUMMARY]))
    src = upload(vault, "a.md", "# A\n\nbody\n")
    first = await ing.ingest(src)
    graph.store.delete(first.fact_id)  # as if it lived in the old store
    again = await ing.ingest(src)
    assert again.status == "new" and again.fact_id != first.fact_id and len(provider.calls) == 2
    assert graph.get(again.fact_id) is not None and ing.sync_log.get(str(src)).fact_id == again.fact_id


async def test_commit_enqueues_document_extraction(vault: Path, ledger: Ledger):
    class Queue:
        def __init__(self):
            self.jobs = []

        def add(self, job):
            self.jobs.append(job)

    ing, graph, provider = make(vault, ledger, ScriptedProvider([SUMMARY]))
    ing.extract_queue = queue = Queue()
    src = upload(vault, "acme-contract.md", "# Acme contract\n\nDana signed.\n")
    res = await ing.ingest(src)
    [job] = queue.jobs
    assert (job.kind, job.fact_id, job.source, job.stores) == ("doc", res.fact_id, "docs/acme-contract.md", None)
    assert (vault / job.source).read_text(encoding="utf-8").startswith("# Acme contract")
    await ing.ingest(src)  # unchanged: nothing more to extract
    assert len(queue.jobs) == 1


async def test_md5_change_resummarizes_and_replaces_the_memory(vault: Path, ledger: Ledger):
    second = completion('{"summary": "Acme contract, renewed.", "names": ["Acme"], "dates": ["2027-12-01"], "facts": ["Net 45 now."]}')
    ing, graph, provider = make(vault, ledger, ScriptedProvider([SUMMARY, second]))
    src = upload(vault, "a.md", "# A\n\nNet 30.\n")
    first = await ing.ingest(src)
    src.write_text("# A\n\nNet 45.\n", encoding="utf-8")
    changed = await ing.rescan()  # the automatic path: nobody named the file
    assert [r.status for r in changed] == ["updated"] and changed[0].fact_id != first.fact_id
    assert graph.get(first.fact_id) is None
    assert [f.text for f in graph.facts(SHARED)][0].startswith("Acme contract, renewed.")
    assert ing.sync_log.get(str(src)).md5 == changed[0].md5
    assert [e.data["status"] for e in ledger.tail(kind="ingest")] == ["new", "updated"]
    assert await ing.rescan() == []


# -- exclusion at every write -----------------------------------------------


async def test_secrets_are_redacted_before_the_vault_and_the_model(vault: Path, ledger: Ledger):
    ing, graph, provider = make(vault, ledger)
    src = upload(vault, "creds.md", "# Setup\n\nUse token ghp_abcdefghijklmnopqrstuvwxyz0123456789ABCD to log in.\n")
    res = await ing.ingest(src)
    assert res.redactions == 1
    md = res.vault_path.read_text(encoding="utf-8")
    assert "ghp_abcdef" not in md and "ghp_abcdef" not in provider.calls[0][1].content
    assert ledger.tail(kind="ingest")[-1].data["redactions"] == 1


async def test_a_summary_that_carries_a_secret_never_reaches_memory(vault: Path, ledger: Ledger):
    leaky = completion('{"summary": "Login with hunter2hunter2.", "names": [], "dates": [], "facts": ["password is hunter2hunter2"]}')
    exclusion.register_secret_value("hunter2hunter2")
    ing, graph, provider = make(vault, ledger, ScriptedProvider([leaky]))
    src = upload(vault, "login.md", "# Login\n\nsee the vault\n")
    with pytest.raises(IngestError):
        await ing.ingest(src)
    assert graph.facts(SHARED) == [] and ing.sync_log.get(str(src)) is None
    assert ledger.tail(kind="ingest")[-1].data["status"] == "refused"


# -- converters --------------------------------------------------------------


async def test_markdownify_converter_routes_by_suffix(vault: Path, ledger: Ledger):
    calls = []

    async def mcp(tool: str, filepath: str) -> str:
        calls.append((tool, filepath))
        return "# From PDF\n\nconverted text\n"

    conv = CompositeConverter([PlainConverter(), MarkdownifyConverter(mcp)])
    ing, graph, provider = make(vault, ledger, converter=conv)
    src = upload(vault, "deck.pdf", "%PDF-1.4 fake")
    res = await ing.ingest(src)
    assert calls == [("pdf-to-markdown", str(src))]
    assert res.vault_path.read_text(encoding="utf-8").startswith("# From PDF")
    assert len(graph.facts(SHARED)) == 1


async def test_unknown_suffix_is_a_clear_refusal(vault: Path, ledger: Ledger):
    ing, graph, provider = make(vault, ledger)
    src = upload(vault, "deck.pptx", "fake")
    with pytest.raises(IngestError, match=r"no converter for ..pptx."):
        await ing.ingest(src)
    assert provider.calls == []


def test_sync_log_round_trips(tmp_path: Path):
    log = SyncLog(tmp_path / "sync.json")
    from src.memory.sync_log import SyncEntry
    log.put(SyncEntry(source="C:/x/a.md", md5="abc", vault_path="docs/a.md", fact_id="f1"))
    assert SyncLog(tmp_path / "sync.json").get("C:/x/a.md").fact_id == "f1"
    assert json.loads((tmp_path / "sync.json").read_text(encoding="utf-8"))["schema"] == VAULT_SCHEMA
    assert log.remove("C:/x/a.md") and log.entries() == []


# -- upload path through the harness -----------------------------------------


async def test_upload_endpoint_ingests_day_one(vault: Path, tmp_path: Path):
    import httpx

    from src.harness.api import Harness, create_app
    from src.harness.bot import Bot
    from src.harness.config import HarnessConfig
    from src.skills.catalog import Catalog

    provider = ScriptedProvider([SUMMARY])
    h = Harness(config=HarnessConfig(vault_root=vault, shell_cwd=tmp_path), provider=provider, ledger=Ledger(vault), catalog=Catalog())
    h.add_bot(Bot(name="Lead", role="lead", id="lead"))
    transport = httpx.ASGITransport(app=create_app(h))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        r = await c.post("/vault/upload", json={"name": "Acme Contract.md", "text": "# Acme\n\nNet 30 with Dana.\n"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "new" and body["vault_path"] == "docs/acme-contract.md"
        assert (vault / "uploads" / "Acme Contract.md").exists() and (vault / "docs" / "acme-contract.md").exists()
        assert [e["vault_path"] for e in (await c.get("/vault/sync")).json()] == ["docs/acme-contract.md"]
        r = await c.post("/vault/upload", json={"name": "../escape.md", "text": "x"})
        assert r.status_code == 422
        facts = (await c.get("/memory", params={"namespace": "shared"})).json()
        assert len(facts) == 1 and facts[0]["source"] == "vault:docs/acme-contract.md"
        assert (await c.post("/vault/rescan")).json() == []


# -- review fixes --------------------------------------------------------------


async def test_same_stem_from_two_sources_lands_in_two_vault_docs(vault: Path, ledger: Ledger):
    ing, graph, provider = make(vault, ledger, ScriptedProvider([SUMMARY, SUMMARY]))
    a = upload(vault, "report.md", "# A report\n")
    b = upload(vault, "Report.txt", "# B report\n")
    ra = await ing.ingest(a)
    rb = await ing.ingest(b)
    assert ra.vault_path == vault / "docs" / "report.md" and rb.vault_path != ra.vault_path
    assert rb.vault_path.parent == vault / "docs" and rb.vault_path.name.startswith("report-")
    assert ra.vault_path.read_text(encoding="utf-8").startswith("# A") and rb.vault_path.read_text(encoding="utf-8").startswith("# B")
    assert (await ing.ingest(b)).vault_path == rb.vault_path  # stable on re-ingest


async def test_rescan_reports_a_refused_doc_and_continues(vault: Path, ledger: Ledger):
    leaky = completion('{"summary": "Login with hunter2hunter2.", "names": [], "dates": [], "facts": []}')
    ing, graph, provider = make(vault, ledger, ScriptedProvider([SUMMARY, SUMMARY, leaky, SUMMARY]))
    a = upload(vault, "a.md", "# A v1\n")
    b = upload(vault, "b.md", "# B v1\n")
    await ing.ingest(a)
    await ing.ingest(b)
    exclusion.register_secret_value("hunter2hunter2")
    a.write_text("# A v2\n", encoding="utf-8")
    b.write_text("# B v2\n", encoding="utf-8")
    results = await ing.rescan()
    assert sorted(r.status for r in results) == ["refused", "updated"]
    refused = next(r for r in results if r.status == "refused")
    assert refused.source == a.resolve() and refused.reason and "blocked" in refused.reason
    assert len(graph.facts(SHARED)) == 2  # a keeps its v1 memory, b was replaced


async def test_upload_never_keeps_a_raw_secret_in_the_vault(vault: Path, tmp_path: Path):
    import base64
    import httpx

    from src.harness.api import Harness, create_app
    from src.harness.config import HarnessConfig
    from src.skills.catalog import Catalog

    provider = ScriptedProvider([SUMMARY, SUMMARY])
    h = Harness(config=HarnessConfig(vault_root=vault, shell_cwd=tmp_path), provider=provider, ledger=Ledger(vault), catalog=Catalog())
    transport = httpx.ASGITransport(app=create_app(h))
    token = "ghp_abcdefghijklmnopqrstuvwxyz0123456789ABCD"
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        r = await c.post("/vault/upload", json={"name": "setup.md", "text": f"# Setup\n\nUse {token} to log in.\n"})
        assert r.status_code == 200 and r.json()["redactions"] == 1
        staged = (vault / "uploads" / "setup.md").read_text(encoding="utf-8")
        assert token not in staged and "Use " in staged  # staged copy is the redacted text
        raw = base64.b64encode(f"binary-ish {token}".encode()).decode()
        r = await c.post("/vault/upload", json={"name": "blob.txt", "content_b64": raw})
        assert r.status_code == 200
        assert token not in (vault / "uploads" / "blob.txt").read_text(encoding="utf-8")
        assert token not in (vault / "docs" / "blob.md").read_text(encoding="utf-8")


# -- markitdown and the summary cap -------------------------------------------


def minimal_docx(text: str) -> bytes:
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                   f'<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
    return buf.getvalue()


async def test_markitdown_converts_html_and_docx(tmp_path: Path):
    c = MarkitdownConverter()
    html = tmp_path / "page.html"
    html.write_text("<h1>Title</h1><p>Hello <b>there</b></p>", encoding="utf-8")
    out = await c.convert(html)
    assert c.accepts(html) and "Title" in out and "Hello" in out
    docx = tmp_path / "note.docx"
    docx.write_bytes(minimal_docx("Quarterly plan"))
    assert c.accepts(docx) and "Quarterly plan" in await c.convert(docx)
    assert not c.accepts(tmp_path / "song.mp3")


async def test_summary_sees_at_most_chunk_chars(vault: Path, ledger: Ledger):
    ing, graph, provider = make(vault, ledger)
    upload(vault, "big.txt", "x" * 50_000)
    await ing.ingest(vault / "uploads" / "big.txt")
    user = provider.calls[0][1].content
    assert len(user) < 16_200 and "document continues" in user


async def test_no_converter_message_names_the_suffix_only():
    with pytest.raises(IngestError, match=r"no converter for '\.xyz'$"):
        await CompositeConverter([PlainConverter()]).convert(Path("a.xyz"))
