"""
Deterministic acronym detection for entity-extraction pipelines.

Scientific corpora introduce most multi-word concepts via the
parenthetical-acronym pattern, e.g.:

    "near-isogenic lines (NILs)"
    "Geographic Information System (GIS)"
    "Special Report on Emissions Scenarios (SRES)"

LLM extraction misses these in two common ways: (1) it picks up only
the acronym or only the full form, not both, and (2) when it does
emit both, they become separate graph nodes with no link. This module
gives the extractor a deterministic head-start: it returns the
(acronym → full form) pairs found in the text so the downstream code
can canonicalize on the full form and store the acronym as an alias.

The detector errs on the side of false negatives over false positives.
It requires that every letter of the acronym appears as the initial
of a word in the full form (in order), which rules out coincidental
parentheticals like "Figure 5 (FIG)" — FIG's letters are F, I, G and
"Figure 5" has only one matching initial.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple


# Acronym shape: starts with uppercase, 2-8 chars total, can include
# digits and hyphens. Optional trailing lowercase 's' for plural form.
_ACR = r"[A-Z][A-Z0-9\-]{1,7}s?"

# Full-form preceding the parenthesis: 1-8 words. Each word starts with
# an alphabetic character and may contain alphanumerics or hyphens.
# We anchor by capturing a span and post-validate via the initials check.
_FULL = r"(?:[A-Za-z][A-Za-z0-9\-]*(?:\s+[A-Za-z][A-Za-z0-9\-]*){0,7})"

# Variant A: "full form (ACR)"
_PATTERN_FULL_THEN_ACR = re.compile(
    rf"(?P<full>{_FULL})\s*\((?P<acr>{_ACR})\)"
)

# Variant B: "ACR (full form)" — much rarer in practice but valid
_PATTERN_ACR_THEN_FULL = re.compile(
    rf"(?<![A-Za-z0-9])(?P<acr>{_ACR})\s*\((?P<full>{_FULL})\)"
)


def _normalize_acronym(acr: str) -> str:
    """Strip optional plural 's' from the acronym for matching."""
    return acr.rstrip("s") if acr.endswith("s") and acr[:-1].isupper() else acr


def _initials_match(acronym: str, full_form: str, *, min_match_ratio: float = 0.6) -> bool:
    """Check that the acronym's letters appear as word initials of the full form.

    The acronym's *uppercase letters* must each be found as the first
    letter of a word in the full form, **in order**. Loose match: at
    least `min_match_ratio` of the acronym letters must align (allows
    for filler words like "of" / "the" that wouldn't contribute).

    Examples that pass:
        "NIL"  vs "near-isogenic lines"            (N-I-L → near, isogenic, lines)
        "SRES" vs "Special Report on Emissions Scenarios"
        "GIS"  vs "Geographic Information System"

    Examples that fail:
        "FIG" vs "Figure 5"          (only F matches)
        "USA" vs "we sat alone"      (case mismatch; uppercase letters
                                      checked against word *initials*
                                      which must be alpha)
    """
    acr_letters = [c for c in _normalize_acronym(acronym) if c.isalpha()]
    if len(acr_letters) < 2:
        return False

    # Split full form into words on whitespace and hyphens (acronyms
    # often include the second half of hyphenated terms — e.g. NIL
    # consumes 'isogenic' from "near-isogenic").
    words = [w for w in re.split(r"[\s\-]+", full_form) if w]
    initials = [w[0].upper() for w in words if w and w[0].isalpha()]

    if not initials:
        return False

    # Walk acronym letters; advance initials pointer when matched.
    matched = 0
    j = 0
    for letter in acr_letters:
        while j < len(initials) and initials[j] != letter:
            j += 1
        if j < len(initials):
            matched += 1
            j += 1

    ratio = matched / len(acr_letters)
    return ratio >= min_match_ratio


def _candidate_pairs(text: str) -> List[Tuple[str, str]]:
    """Yield (acronym, full_form) candidates from both patterns."""
    out: List[Tuple[str, str]] = []
    for m in _PATTERN_FULL_THEN_ACR.finditer(text):
        out.append((m.group("acr"), m.group("full").strip()))
    for m in _PATTERN_ACR_THEN_FULL.finditer(text):
        out.append((m.group("acr"), m.group("full").strip()))
    return out


def find_acronym_pairs(text: str) -> Dict[str, str]:
    """Detect (acronym → full form) pairs in *text*.

    Only returns pairs that pass the initials check. When the same
    acronym appears multiple times with different full forms, the
    first detected expansion wins (typical convention: scientific
    papers define an acronym once at first use).
    """
    if not text:
        return {}

    pairs: Dict[str, str] = {}
    for acr, full in _candidate_pairs(text):
        if not _initials_match(acr, full):
            continue
        # Trim filler words (single-char tokens like 'a' / 'A' aren't
        # informative as the leading word of an expansion).
        full_words = full.split()
        while full_words and len(full_words[0]) <= 1:
            full_words.pop(0)
        if not full_words:
            continue
        cleaned_full = " ".join(full_words)
        # Stable: first occurrence wins
        pairs.setdefault(acr, cleaned_full)
    return pairs


def format_acronym_hints(pairs: Dict[str, str]) -> str:
    """Render a short prompt-friendly string of detected pairs.

    Returns the empty string when *pairs* is empty so prompts can
    conditionally include / skip the section without templating mess.
    """
    if not pairs:
        return ""
    lines = [f'  - "{full}" → acronym "{acr}"' for acr, full in pairs.items()]
    return "\n".join(lines)


def canonicalize_name(name: str, pairs: Dict[str, str]) -> str:
    """Map an entity name to its canonical full form if it's a known acronym.

    Case-insensitive match. Returns *name* unchanged when no mapping
    applies.
    """
    if not name or not pairs:
        return name
    stripped = _normalize_acronym(name.strip())
    for acr, full in pairs.items():
        if _normalize_acronym(acr).lower() == stripped.lower():
            return full
    return name
