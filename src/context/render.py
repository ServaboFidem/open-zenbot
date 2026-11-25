"""Render a transcript to plain text, tool calls included. Shared by
compaction (what the generator summarizes) and supervision (what the
decider judges). No generator import here: the decider package must
stay prose-free."""

from __future__ import annotations

import json
import re

from src.providers.interface import Message

# a file attached in chat (see src/harness/chat_turn.py): a transcript keeps its size, never its content
FILE_BLOCK = re.compile(r'<<<file name="([^"\n]*)">>>\n.*?\n<<<end of file>>>', re.S)
PINNED_HEADER = "## Protected task (pinned)"


def is_pinned(m: Message) -> bool:
    return m.role == "system" and m.content.startswith(PINNED_HEADER)


def render_transcript(messages: list[Message], *, skip_pinned: bool = True) -> str:
    out: list[str] = []
    for m in messages:
        if skip_pinned and is_pinned(m):
            continue
        if m.role == "tool":
            out.append(f"[tool result {m.name or ''} {m.tool_call_id or ''}]\n{m.content}")
            continue
        text = f"[{m.role}]"
        if m.content:
            text += "\n" + FILE_BLOCK.sub(lambda f: f"[content of {f.group(1)}, {len(f.group(0))} characters]", m.content)
        for c in m.tool_calls:
            text += f"\n[tool call {c.name} {c.id}] {json.dumps(c.arguments, ensure_ascii=False)}"
        out.append(text)
    return "\n\n".join(out)
