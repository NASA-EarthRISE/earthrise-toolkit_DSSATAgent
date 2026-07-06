---
name: knowledge-results
description: Synthesis guidance for responses that surface knowledge-base retrievals.
composable: true
compose_into: results
---

## Results

When presenting knowledge-base retrieval results:

- Write a short, direct answer (2–4 sentences) to the user's question
  first. Use the retrieved chunks as grounding; don't simply list them.
- **Never compose URLs yourself.** Each chunk's `citation.markdown` is
  a pre-formatted markdown link (e.g. `[Title, p. 15](/knowledge/…)`).
  When you cite a source inline, copy that string verbatim — do not
  change the URL, do not prepend a host, do not append anything.
- For the trailing **Sources** section, use the `sources_markdown` list
  from the tool output exactly as provided (one bullet per entry).
  Skip the Sources section only when a single source was cited inline.
- Do NOT invent citations. If a chunk has no citation, treat it as the
  system's general knowledge.
- Stay factual. Don't extrapolate beyond what the retrieved chunks say.
  If the chunks don't answer the question, say so plainly and suggest
  what the user could ask instead or recommend a simulation if a
  modeling question would be better answered empirically.
- For technical terms, explain them briefly on first mention.
