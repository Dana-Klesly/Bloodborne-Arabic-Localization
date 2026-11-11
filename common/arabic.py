"""Arabic text helpers: character classes, normalization for matching, and display shaping.

Storage rule of this project:
  The database always stores LOGICAL Arabic text (normal Unicode letters U+0600–U+06FF,
  typing order). Shaping (connected letter forms) and RTL reordering happen only when
  producing output (preview now, mod build later). Never store shaped text.
"""
import re
import unicodedata

# ---------- character classes ----------
ARABIC_LETTERS = re.compile(r"[ء-غف-يٱ-ۓ]")
HARAKAT = re.compile(r"[ً-ْٰ]")
TATWEEL = "ـ"
# Presentation forms = already-shaped glyph codepoints. Must not be stored in the DB.
PRESENTATION_FORMS = re.compile(r"[ﭐ-﷿ﹰ-﻿]")
# Invisible direction controls. Sometimes needed, but usually copied in by accident.
BIDI_CONTROLS = re.compile(r"[‎‏‪-‮⁦-⁩؜]")
ZERO_WIDTH = re.compile(r"[​‌‍⁠]")
# Persian / Urdu letters that look like Arabic ones (frequent machine-translation error).
NON_ARABIC_LOOKALIKES = {
    "ی": "ي (Persian Yeh ی)",
    "ک": "ك (Keheh ک)",
    "ہ": "ه (Heh Goal ہ)",
    "ھ": "ه (Heh Doachashmee ھ)",
}
ARABIC_INDIC_DIGITS = re.compile(r"[٠-٩۰-۹]")
LATIN_LETTERS = re.compile(r"[A-Za-z]")
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")


def has_arabic(text: str) -> bool:
    return bool(text and ARABIC_LETTERS.search(text))


# ---------- normalization (for comparisons only, never for storage) ----------
_ALEF_VARIANTS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا"})


def normalize_for_match(text: str) -> str:
    """Loose form for glossary matching: no harakat/tatweel, unified alef, alef maqsura→ya."""
    if not text:
        return ""
    t = unicodedata.normalize("NFC", text)
    t = HARAKAT.sub("", t).replace(TATWEEL, "")
    t = t.translate(_ALEF_VARIANTS).replace("ى", "ي")
    return t


def definite_forms(term: str) -> list[str]:
    """Forms a term can take after Arabic clitics, for loose glossary matching.

    'الصياد' should also match 'للصياد' (li + al) and 'صياد' (indefinite).
    """
    t = normalize_for_match(term).strip()
    forms = {t}
    words = t.split()
    if words and words[0].startswith("ال") and len(words[0]) > 3:
        bare = " ".join([words[0][2:]] + words[1:])
        forms.add(bare)                 # indefinite / construct
        forms.add("لل" + bare)          # li + al
    return sorted(forms, key=len, reverse=True)


# ---------- display shaping ----------
def _reshaper(delete_harakat: bool = False):
    import arabic_reshaper
    cfg = {"delete_harakat": delete_harakat, "support_ligatures": True}
    return arabic_reshaper.ArabicReshaper(configuration=cfg)


_PUA_START = 0xE000


def _protect(line: str):
    """Swap each placeholder/code for one Private-Use character (bidi class L).

    Without this the bidi algorithm reorders the inside of codes: '%s' became 's%'.
    """
    from common.placeholders import _COMBINED
    tokens = []

    def repl(m):
        tokens.append(m.group(0))
        return chr(_PUA_START + len(tokens) - 1)

    return _COMBINED.sub(repl, line), tokens


def _restore(line: str, tokens: list[str]) -> str:
    for i, tok in enumerate(tokens):
        line = line.replace(chr(_PUA_START + i), tok)
    return line


def shape_for_display(text: str, delete_harakat: bool = False, base_dir: str = "R",
                      protect_codes: bool = True) -> str:
    """Logical text -> visual text (joined letter forms + RTL order), line by line.

    Lines are processed separately so a multi-line string keeps its line order.
    base_dir='R' treats every line as a right-to-left paragraph.
    protect_codes keeps placeholders like {0}, %s, <?...?> intact and unreordered.
    """
    from bidi.algorithm import get_display
    reshaper = _reshaper(delete_harakat)
    out = []
    for line in (text or "").split("\n"):
        tokens = []
        if protect_codes:
            line, tokens = _protect(line)
        shaped = get_display(reshaper.reshape(line), base_dir=base_dir)
        out.append(_restore(shaped, tokens))
    return "\n".join(out)
