---
name: knowledge-search
description: Search the documentation knowledge base for conceptual or factual answers.
tools:
  - knowledge_retrieve
---

## Playbook

Invoke `knowledge_retrieve` when the user asks a conceptual or factual
question whose answer lives in documentation — domain theory, system
behavior, parameter definitions, or "how does X work" / "what is Y" style
questions.

Do NOT use `knowledge_retrieve` for:
- Running a task or computation (use the relevant task skill).
- Listing system capabilities (use `list_capabilities`).
- Querying results of a specific past task (use the relevant query skill).

### Strategy selection

Leave `strategy` unset unless the question shape clearly warrants a
non-default:

- **factual lookup** ("what is the value of parameter X?") → `bm25` or `basic_vector`
- **broad conceptual explanation** ("how does the model represent process X?") → `hybrid` or `raptor`
- **comparative / cross-doc synthesis** ("compare X vs Y") → `rag_fusion`
- **multi-hop reasoning** ("does factor A interact with factor B?") → `hyde` or `graphrag`

When unset, the tool resolves the user's preference, then the system
default — never hardcoded to `hybrid`.

### top_k

Default 5 covers most questions. Raise to 10–15 when the user asks for a
broad summary of what the documentation says about a topic. Keep at 5 for
targeted factual questions.

### Handling results

The tool returns a list of chunks, each with `content` and `metadata`
(typically `citation` with source_title, page_number, section_title, url).
Synthesize a concise answer that weaves the chunks together and preserve
citations — use "(Source: …)" tags inline or a **Sources** block at the end
with linked references when a URL is present.

If the tool returns `status: error`, tell the user the knowledge base is
currently unavailable and suggest trying a different question or another
task instead.
