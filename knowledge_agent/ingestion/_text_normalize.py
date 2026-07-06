"""
Page-text normalization for column-based PDFs.

PyMuPDF returns text as it appears on the page: hard newlines at column
edges, hyphenated line breaks inside words, and physical-order
concatenation when reading order isn't perfectly inferred. Feeding that
to a chunker that prefers single-newline boundaries produces fragments
that physically end mid-sentence, which hurts LLM entity extraction.

This module fixes the two highest-impact damage modes:
  1) Hyphenated line breaks: "pre-\\nemerge" → "preemerge" (when both
     halves are lowercase — preserves legitimate compounds like "non-
     DSSAT" where the second half is capitalized).
  2) Soft wraps inside a paragraph: a single \\n between two lowercase
     letters becomes a space; paragraph breaks (\\n\\n) are preserved.

Column reading-order is handled separately by the PDF parser via the
block-sort path; this module operates on the already-merged text.
"""

from __future__ import annotations

import re


# Word-internal hyphenated line break: "pre-\nemerge". Only collapses
# when both flanking characters are lowercase ASCII letters — that
# rules out hyphenated proper nouns ("non-DSSAT"), section refs ("Eq-3"),
# and unit constructs ("g m-2\nday-1") where one side is uppercase or a
# digit.
_HYPHEN_LINEBREAK_RE = re.compile(r"([a-z])-\n([a-z])")

# Soft-wrap inside a sentence: lowercase letter / comma / semicolon at
# end of line followed by a lowercase letter at start of the next.
# Excludes lines ending in sentence-final punctuation (. ! ?) and
# next-line starts that look like list bullets or numbered items.
_SOFT_WRAP_RE = re.compile(r"([a-z,;])\n(?![ \t]*[•\-•\d])([a-z])")

# Paragraph-break placeholder: protect double-newlines from the
# soft-wrap pass by swapping in a sentinel first, then restoring.
_PARA_SENTINEL = "\x00PARA\x00"
_PARA_BREAK_RE = re.compile(r"\n[ \t]*\n+")

# Page-header / page-footer noise that some PDFs emit as their own
# line: numeric-only lines that look like page numbers, or short
# uppercase strings that look like running heads. Conservative — only
# strips lines that are *just* a 1-3 digit number on their own.
_PAGE_NUMBER_ONLY_RE = re.compile(r"(?m)^\s*\d{1,3}\s*$")


def dehyphenate(text: str) -> str:
    """Collapse word-internal hyphenated line breaks.

    "pre-\\nemerge" → "preemerge". Preserves legitimate hyphenation
    where either side is uppercase or non-alpha.
    """
    return _HYPHEN_LINEBREAK_RE.sub(r"\1\2", text)


def reflow_soft_wraps(text: str) -> str:
    """Convert intra-paragraph single newlines into spaces.

    Paragraph breaks (\\n\\n) are preserved. Lines ending in sentence-
    final punctuation (. ! ?) keep their newline so headings and
    sentence-final layouts aren't accidentally merged.
    """
    # Protect paragraph breaks
    protected = _PARA_BREAK_RE.sub(_PARA_SENTINEL, text)
    # Collapse soft wraps
    reflowed = _SOFT_WRAP_RE.sub(r"\1 \2", protected)
    # Restore paragraph breaks
    return reflowed.replace(_PARA_SENTINEL, "\n\n")


def strip_page_number_lines(text: str) -> str:
    """Drop lines that are just a bare page number (1–3 digits)."""
    return _PAGE_NUMBER_ONLY_RE.sub("", text)


def normalize_page_text(text: str) -> str:
    """Apply the full normalization pipeline to a single page's text.

    Order matters: dehyphenate first (consumes a \\n that the soft-wrap
    pass would otherwise see), then soft-wrap reflow, then strip
    standalone page numbers, then collapse runs of whitespace inside
    each line.
    """
    if not text:
        return text
    text = dehyphenate(text)
    text = reflow_soft_wraps(text)
    text = strip_page_number_lines(text)
    # Collapse runs of horizontal whitespace within a line (PDFs often
    # emit double-spaces between column-extracted tokens).
    text = re.sub(r"[ \t]{2,}", " ", text)
    # Trim trailing whitespace per line, and collapse 3+ newlines down to 2.
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
