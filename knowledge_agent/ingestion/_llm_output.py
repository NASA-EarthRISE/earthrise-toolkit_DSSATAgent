"""
Helpers for normalizing LLM completion text before it gets stored.

Common need: instruction-following LLMs (especially llama family) like to
prepend a brief "Here is X:" preamble to their output, even when the
prompt explicitly forbids it. Storing those preambles in the
contextual-enrichment prefix or RAPTOR summary nodes pollutes embeddings
and makes user-facing displays awkward.

`strip_llm_preamble()` is a defensive post-processor used at every LLM
output site that produces narrative content (contextual, RAPTOR summary,
HyDE, RAG-Fusion variants). It does NOT touch outputs that are
structured JSON — those have their own parsing path.
"""

from __future__ import annotations

import re


# Patterns matched at the start of the string. Order matters: longer
# more-specific patterns first so they win over shorter generic ones.
_PREAMBLE_PATTERNS = [
    # "Here is a concise paragraph that captures the key concepts and relationships: ..."
    r"^here\s+(?:is|are|'?s)\s+(?:an?\s+|the\s+)?(?:concise\s+|short\s+|brief\s+)?(?:paragraph|summary|description|context\s+description|answer|response|overview|explanation)(?:\s+that[^:]*)?(?:\s+for[^:]*)?[:\-—]\s*",
    # "Here's the context description: ..."
    r"^here\s+(?:is|are|'?s)\s+(?:the\s+|a\s+|an\s+)?(?:context\s+description|context|summary|paragraph|answer|response)[:\-—]\s*",
    # "Below is..." / "The following is..."
    r"^(?:below|the\s+following)\s+(?:is|are)\s+(?:a\s+|the\s+|an\s+)?[^:\n]{0,80}[:\-—]\s*",
    # "Sure! Here is..." / "Of course, here is..."
    r"^(?:sure|of\s+course|certainly|absolutely)[!,.\s]+here(?:'?s|\s+is|\s+are)?[^:\n]{0,80}[:\-—]\s*",
    # "I'll write..." / "I can summarize..."
    r"^(?:i'?ll|i\s+can|i\s+will|let\s+me)\s+(?:write|summarize|provide|give|explain|describe)[^:\n]{0,80}[:\-—]\s*",
    # Bare "Summary:" / "Context description:" / "Answer:" at the start
    r"^(?:summary|context\s+description|context|answer|response|paragraph|description|overview)[:\-—]\s*",
]


_compiled = [re.compile(p, re.IGNORECASE) for p in _PREAMBLE_PATTERNS]


def strip_llm_preamble(text: str) -> str:
    """Strip leading meta-commentary preamble from an LLM completion.

    Idempotent: re-running on already-clean text returns it unchanged.
    Only strips ONE preamble pattern per call (no greedy looping), so a
    legitimate sentence that happens to start with "Summary:" inside the
    middle of paragraph text is preserved.

    Edge case behavior:
    - Empty / whitespace-only input → returned as-is
    - Multi-paragraph input → only the leading whitespace + preamble is
      stripped; subsequent paragraphs are preserved exactly
    """
    if not text:
        return text

    stripped = text.lstrip()
    for pattern in _compiled:
        m = pattern.match(stripped)
        if m:
            return stripped[m.end():].lstrip()
    return stripped
