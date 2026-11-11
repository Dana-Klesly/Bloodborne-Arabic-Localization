"""Detect placeholders, variables and markup codes that must survive translation unchanged.

The exact codes used by the game's text files are NOT confirmed yet. This module
covers the common families; once real extracted text is available, add the real
patterns to PATTERNS (order matters: longer / more specific patterns first).
"""
import re
from collections import Counter

# (name, regex). Each match is treated as one protected token.
PATTERNS = [
    ("xml_pi", r"<\?[^?>]*\?>"),                 # <?gaitem@1?>   style codes
    ("tag", r"</?[A-Za-z][A-Za-z0-9_]*(?:\s+[^<>]*)?/?>"),  # <font color="#fff">, </font>, <br/>
    ("brace", r"\{[A-Za-z0-9_:.,\-]*\}"),        # {0}  {name}  {1:00}
    ("printf", r"%(?:\d+\$)?[-+ #0]*\d*(?:\.\d+)?[sdifuxXc%]"),  # %s %d %1$s %%
    ("dollar", r"\$[A-Za-z_][A-Za-z0-9_]*\$?"),  # $VAR or $VAR$
    ("bracket_code", r"\[[A-Z][A-Z0-9_]*\]"),    # [BUTTON_A]
    ("escape", r"\\[nrt]"),                      # literal backslash-n written in text
]

_COMBINED = re.compile("|".join(f"(?P<{n}>{p})" for n, p in PATTERNS))


def find_tokens(text: str) -> list[str]:
    """Return protected tokens in order of appearance."""
    if not text:
        return []
    return [m.group(0) for m in _COMBINED.finditer(text)]


def token_counter(text: str) -> Counter:
    return Counter(find_tokens(text))


def strip_tokens(text: str, replacement: str = " ") -> str:
    """Remove protected tokens (used before checking letters / glossary)."""
    if not text:
        return ""
    return _COMBINED.sub(replacement, text)


def compare_tokens(source: str, translation: str) -> tuple[Counter, Counter]:
    """Return (missing, extra): tokens lost from the source / tokens not present in the source.

    Order is NOT compared on purpose: Arabic word order legitimately differs.
    """
    src, trg = token_counter(source), token_counter(translation)
    return src - trg, trg - src
