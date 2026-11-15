"""Work docs into the vault and into memory.

`ingest(path)` takes a file path so an upload, a watcher, or a Drive
connector can all feed it. The path must sit under MD_ALLOWED_PATHS
(default: the vault's `uploads` folder). The file is converted to
Markdown: plain text formats are read as they are; documents (PDF,
Office, HTML, data files) go through markitdown in a worker thread;
what is left (images, audio) goes through the markdownify MCP when it
is connected, picked by suffix. Secrets are
redacted before the Markdown is written or shown to the generator. The
vault layout is one function, `vault_path_for`. The generator writes a
short summary with names, dates, and facts; memory keeps that plus a
`vault:` pointer, never the full text. The sync log keeps each file's
md5, and `rescan()` re-summarizes whatever changed. With an extraction
queue wired (the Hindsight store), each committed file also queues a `doc` job:
its full text is routed and extracted into the graph in the background.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from .exclusion import ExclusionError, exclusion
from .graph import SHARED, MemoryGraph, Tier
from .sync_log import VAULT_SCHEMA, SyncEntry, SyncLog

__all__ = ["CompositeConverter", "IngestError", "IngestResult", "Ingestor", "Prepared", "MarkdownifyConverter", "MarkitdownConverter",
           "PlainConverter", "VAULT_SCHEMA", "allowed_paths", "vault_path_for"]

PLAIN_SUFFIXES = {".md", ".markdown", ".txt", ".text"}
MARKDOWNIFY_TOOLS = {  # suffix -> markdownify MCP tool
    ".pdf": "pdf-to-markdown", ".docx": "docx-to-markdown", ".xlsx": "xlsx-to-markdown", ".pptx": "pptx-to-markdown",
    ".png": "image-to-markdown", ".jpg": "image-to-markdown", ".jpeg": "image-to-markdown",
    ".mp3": "audio-to-markdown", ".wav": "audio-to-markdown", ".m4a": "audio-to-markdown",
}
MARKITDOWN_SUFFIXES = {".pdf", ".docx", ".xlsx", ".pptx", ".html", ".htm", ".csv", ".json", ".xml", ".epub"}
SLUG = re.compile("[^a-z0-9]+")


class IngestError(ValueError):
    pass


def vault_path_for(vault_root: Path, source: Path) -> Path:
    """The one place the vault layout is decided (schema VAULT_SCHEMA):
    every work doc lands as `docs/<slug>.md`."""
    slug = SLUG.sub("-", source.stem.lower()).strip("-") or "doc"
    return Path(vault_root) / "docs" / f"{slug}.md"


def allowed_paths(vault_root: Path) -> list[Path]:
    raw = os.environ.get("MD_ALLOWED_PATHS", "")
    paths = [Path(p).expanduser() for p in raw.split(os.pathsep) if p.strip()]
    return [p.resolve() for p in paths] or [(Path(vault_root) / "uploads").resolve()]


# -- converters --------------------------------------------------------------


class Converter(Protocol):
    def accepts(self, path: Path) -> bool: ...
    async def convert(self, path: Path) -> str: ...


class PlainConverter:
    def accepts(self, path: Path) -> bool:
        return path.suffix.lower() in PLAIN_SUFFIXES

    async def convert(self, path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="replace")


class MarkitdownConverter:
    """Microsoft's markitdown, in process. Synchronous and CPU-bound, so
    each conversion runs in a worker thread."""

    def __init__(self) -> None:
        self._md: Any = None

    def accepts(self, path: Path) -> bool:
        return path.suffix.lower() in MARKITDOWN_SUFFIXES

    async def convert(self, path: Path) -> str:
        if self._md is None:
            from markitdown import MarkItDown

            self._md = MarkItDown()
        return str(await asyncio.to_thread(lambda: self._md.convert(str(path)).text_content))


class MarkdownifyConverter:
    """Calls the markdownify MCP: `call(tool_name, filepath) -> markdown`.
    The harness binds `call` to its MCP client; tests bind a stub."""

    def __init__(self, call: Callable[[str, str], Awaitable[str]]) -> None:
        self.call = call

    def accepts(self, path: Path) -> bool:
        return path.suffix.lower() in MARKDOWNIFY_TOOLS

    async def convert(self, path: Path) -> str:
        return str(await self.call(MARKDOWNIFY_TOOLS[path.suffix.lower()], str(path)))


class CompositeConverter:
    def __init__(self, converters: list[Any]) -> None:
        self.converters = converters

    def accepts(self, path: Path) -> bool:
        return any(c.accepts(path) for c in self.converters)

    async def convert(self, path: Path) -> str:
        for c in self.converters:
            if c.accepts(path):
                return await c.convert(path)
        raise IngestError(f"no converter for {path.suffix!r}")


# -- the ingestor ------------------------------------------------------------


def _capped(markdown: str, limit: int) -> str:
    """One bounded summary call per file: the head of the document, and a
    note about the rest."""
    if len(markdown) <= limit:
        return markdown
    tail = f"[document continues; {len(markdown) - limit} more characters]"
    return markdown[:limit] + "\n\n" + tail


@dataclass
class IngestResult:
    status: str  # new | updated | unchanged | refused (rescan only)
    source: Path
    vault_path: Path
    md5: str
    fact_id: str
    redactions: int = 0
    reason: str = ""  # set when status is refused

    def to_dict(self, vault_root: Path) -> dict[str, Any]:
        rel = self.vault_path.relative_to(vault_root).as_posix() if self.vault_path.is_relative_to(vault_root) else None
        return {"status": self.status, "source": str(self.source), "vault_path": rel,
                "md5": self.md5, "fact_id": self.fact_id, "redactions": self.redactions, "reason": self.reason}


@dataclass
class Prepared:
    """A converted, summarized file, not yet written anywhere."""
    source: Path
    md5: str
    previous: SyncEntry | None
    vault_path: Path
    rel: str
    markdown: str
    redactions: int
    summary: Any
    text: str
    bot_id: str
    session_id: str


class Ingestor:
    def __init__(self, vault_root: Path, converter: Any, generator: Any, graph: MemoryGraph, ledger: Any,
                 sync_log: SyncLog | None = None, allowed: list[Path] | None = None, offload: bool = False,
                 extract_queue: Any = None) -> None:
        self.vault_root = Path(vault_root)
        self.extract_queue = extract_queue  # an ExtractQueue, or None (the local store: the summary fact is all)
        self.offload = offload  # run the blocking graph writes in a worker thread (a directory import)
        self.converter = converter
        self.generator = generator
        self.graph = graph
        self.ledger = ledger
        self.sync_log = sync_log or SyncLog(self.vault_root / "sync.json")
        self.allowed = [Path(p).resolve() for p in allowed] if allowed else allowed_paths(self.vault_root)

    async def _graph(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """The store write blocks (a Hindsight retain waits on the server); off the loop when asked."""
        if self.offload:
            return await asyncio.to_thread(lambda: fn(*args, **kwargs))
        return fn(*args, **kwargs)

    def _refuse(self, source: Path, reason: str, bot_id: str, session_id: str) -> IngestError:
        self.ledger.append("ingest", bot_id, session_id, status="refused", source=str(source), reason=reason)
        return IngestError(reason)

    def _check_sandbox(self, source: Path) -> Path:
        resolved = source.resolve()
        if not any(resolved == a or a in resolved.parents for a in self.allowed):
            raise IngestError(f"{source} is outside MD_ALLOWED_PATHS ({os.pathsep.join(str(a) for a in self.allowed)})")
        if not resolved.is_file():
            raise IngestError(f"{source} is not a file")
        return resolved

    def _claim_vault_path(self, source: Path) -> Path:
        """The layout's path, unless another source already owns it; then
        the slug gains a short hash of this source's own path, stable across re-ingests."""
        path = vault_path_for(self.vault_root, source)
        rel = path.relative_to(self.vault_root).as_posix()
        owner = next((e for e in self.sync_log.entries() if e.vault_path == rel), None)
        if owner is None or owner.source == str(source):
            return path
        tag = hashlib.md5(str(source).encode("utf-8")).hexdigest()[:6]
        return path.with_name(f"{path.stem}-{tag}.md")

    async def ingest(self, path: Path | str, *, bot_id: str = "user", session_id: str = "") -> IngestResult:
        prepared = await self.prepare(path, bot_id=bot_id, session_id=session_id)
        if isinstance(prepared, IngestResult):
            return prepared
        return await self.commit(prepared)

    async def prepare(self, path: Path | str, *, bot_id: str = "user", session_id: str = "") -> "Prepared | IngestResult":
        """Everything before the first write: sandbox, convert, redact,
        summarize. Nothing is written, so it is safe to cancel at any await.
        An unchanged file comes back as its final IngestResult."""
        source = Path(path)
        try:
            source = self._check_sandbox(source)
            if not self.converter.accepts(source):
                raise IngestError(f"no converter for {source.suffix!r}")
        except IngestError as e:
            raise self._refuse(source, str(e), bot_id, session_id) from None
        md5 = hashlib.md5(source.read_bytes()).hexdigest()
        previous = self.sync_log.get(str(source))
        if previous is not None and self.graph.get(previous.fact_id) is None:
            previous = None  # its fact is not in this store (an upgrade from cognee, or forgotten): import it again
        vault_path = self._claim_vault_path(source)
        if previous is not None and previous.md5 == md5:
            self.ledger.append("ingest", bot_id, session_id, status="unchanged", source=str(source), md5=md5, fact_id=previous.fact_id)
            return IngestResult("unchanged", source, self.vault_root / previous.vault_path, md5, previous.fact_id)
        markdown, found = exclusion.redact(await self.converter.convert(source))
        try:
            exclusion.require({"kind": "vault_doc", "text": markdown}, "ingest")
            summary = await self.generator.vault_summary(_capped(markdown, self.generator.chunk_chars), title=source.stem)
            rel = vault_path.relative_to(self.vault_root).as_posix()
            text = _memory_text(summary, rel)
            exclusion.require({"kind": "memory", "text": text, "names": summary.names}, "ingest")
        except ExclusionError as e:
            raise self._refuse(source, str(e), bot_id, session_id) from None
        return Prepared(source, md5, previous, vault_path, rel, markdown, len(found), summary, text, bot_id, session_id)

    async def commit(self, p: "Prepared") -> IngestResult:
        """The writes: vault file, graph (forget the old fact, remember the
        new), sync log, ledger. Run it to the end once started; a stop
        between the graph write and the sync log would orphan a fact."""
        p.vault_path.parent.mkdir(parents=True, exist_ok=True)
        p.vault_path.write_text(p.markdown, encoding="utf-8")
        if p.previous is not None:
            await self._graph(self.graph.forget, p.previous.fact_id, bot_id=p.bot_id, session_id=p.session_id)
        fact = await self._graph(self.graph.remember, p.text, SHARED, Tier.project, bot_id=p.bot_id, entities=p.summary.names,
                                 source=f"vault:{p.rel}", session_id=p.session_id)
        self.sync_log.put(SyncEntry(source=str(p.source), md5=p.md5, vault_path=p.rel, fact_id=fact.id, chars=len(p.markdown)))
        if self.extract_queue is not None:
            from .extract_queue import new_job  # not at import time: extract_queue -> profile -> router imports this module

            self.extract_queue.add(new_job("doc", fact.id, p.rel))
        status = "updated" if p.previous is not None else "new"
        self.ledger.append("ingest", p.bot_id, p.session_id, status=status, source=str(p.source), md5=p.md5, fact_id=fact.id,
                           vault_path=p.rel, redactions=p.redactions, chars=len(p.markdown), parsed=p.summary.parsed)
        return IngestResult(status, p.source, p.vault_path, p.md5, fact.id, redactions=p.redactions)

    async def rescan(self, *, bot_id: str = "user", session_id: str = "") -> list[IngestResult]:
        """Re-ingest every logged file whose bytes changed. A file that
        vanished keeps its memory until someone forgets it on purpose."""
        out: list[IngestResult] = []
        for entry in self.sync_log.entries():
            src = Path(entry.source)
            if not src.is_file():
                continue
            if hashlib.md5(src.read_bytes()).hexdigest() != entry.md5:
                try:
                    out.append(await self.ingest(src, bot_id=bot_id, session_id=session_id))
                except IngestError as e:  # already in the ledger as refused; the next doc still gets its turn
                    out.append(IngestResult("refused", src.resolve(), self.vault_root / entry.vault_path, entry.md5, entry.fact_id, reason=str(e)))
        return out


def _memory_text(summary: Any, rel: str) -> str:
    parts = [summary.summary.strip()]
    if summary.names:
        parts.append("Names: " + ", ".join(summary.names) + ".")
    if summary.dates:
        parts.append("Dates: " + ", ".join(summary.dates) + ".")
    if summary.facts:
        parts.append("Facts: " + " ".join(f.rstrip(".") + "." for f in summary.facts))
    parts.append(f"(vault:{rel})")
    return " ".join(parts)
