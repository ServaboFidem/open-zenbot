---
name: Research brief
slug: research-brief
use: When asked to research a topic, question, or product and produce a short structured brief.
inputs:
  - topic: the question or subject to research
  - audience: who will read the brief (optional, default is the user)
access:
  - shell
steps:
  - Pin down the question and who it is for. Do not recite it back to the reader.
  - Gather evidence with the tools available. Prefer primary sources.
  - Separate confirmed facts from assumptions. Say which is which, in plain words.
  - Tell the reader the three things that matter most, what is still open, and one concrete next step, as a conversation rather than labelled sections.
validation:
  - Every finding says where it came from.
  - The brief is under 400 words.
return: A Markdown brief that reads like a knowledgeable friend talking it through, with sources as links at the end.
approvals:
  - "shell:git push*"
  - "shell:rm *"
  - "shell:curl * -X POST*"
band_width: 0.1
---
# Research brief

Turn a question into something the reader can act on. Do not pad. If the
evidence is thin, say so in the open questions rather than guessing.

When the shell is the only tool, use it to read local files and run
read-only commands. Anything that sends, publishes, or deletes waits for
approval.
