"""Single-string checks. Each check returns a list of Issue objects.

Severities
  error    must be fixed before a string can be approved
  warning  probably wrong, a human must look at it
  info     worth knowing, often fine
"""
import unicodedata
from dataclasses import dataclass

from common import arabic as ar
from common.placeholders import compare_tokens, strip_tokens

LENGTH_RATIO_WARNING = 1.8   # Arabic noticeably longer than English with no known limit
LATIN_PUNCT = {",": "،", ";": "؛", "?": "؟"}


@dataclass(frozen=True)
class Issue:
    severity: str
    check: str
    message: str


def _e(check, msg):
    return Issue("error", check, msg)


def _w(check, msg):
    return Issue("warning", check, msg)


def _i(check, msg):
    return Issue("info", check, msg)


# ------------------------------------------------------------------ empty / untranslated
def check_empty(source: str, translation, status: str) -> list[Issue]:
    issues = []
    if source is None or source.strip() == "":
        issues.append(_w("empty", "English source is empty"))
    if status != "pending" and (translation is None or translation.strip() == ""):
        issues.append(_e("empty", f"status is '{status}' but the translation is empty"))
    if translation and source and translation.strip() == source.strip() and ar.LATIN_LETTERS.search(source):
        issues.append(_w("untranslated", "translation is identical to the English source"))
    if translation and source and not ar.has_arabic(translation) and ar.LATIN_LETTERS.search(strip_tokens(source)):
        issues.append(_e("untranslated", "translation contains no Arabic letters"))
    if translation and source:
        if translation != translation.strip() and source == source.strip():
            issues.append(_w("whitespace", "translation has leading/trailing spaces the source does not have"))
    return issues


# ------------------------------------------------------------------ placeholders / codes
def check_placeholders(source: str, translation: str) -> list[Issue]:
    issues = []
    missing, extra = compare_tokens(source, translation)
    if missing:
        issues.append(_e("placeholders", "missing or changed codes: " +
                         ", ".join(f"{t!r}×{n}" for t, n in missing.items())))
    if extra:
        issues.append(_e("placeholders", "codes not in the source: " +
                         ", ".join(f"{t!r}×{n}" for t, n in extra.items())))
    if source.count("\n") != translation.count("\n"):
        issues.append(_w("line_breaks", f"line breaks: source {source.count(chr(10))}, "
                                        f"translation {translation.count(chr(10))}"))
    return issues


# ------------------------------------------------------------------ glossary
def check_glossary(source: str, translation: str, terms) -> list[Issue]:
    """terms: list of glossary.glossary.Term (already filtered with terms_in or not)."""
    from glossary.glossary import terms_in
    issues = []
    for t in terms_in(source, terms):
        make = _e if t.status == "approved" else _w
        if not t.used_in(translation):
            make_msg = f"'{t.term_en}' should be translated as '{t.approved_ar}'"
            if t.variants_ar:
                make_msg += f" (or: {' / '.join(t.variants_ar)})"
            issues.append(make("glossary", make_msg + f" [{t.status}]"))
        for bad in t.forbidden_used(translation):
            issues.append(make("glossary_forbidden",
                               f"'{bad}' is a forbidden translation of '{t.term_en}', use '{t.approved_ar}'"))
    return issues


# ------------------------------------------------------------------ length
def check_length(source: str, translation: str, max_length, max_line_length) -> list[Issue]:
    issues = []
    n = len(translation)
    if max_length is not None and n > max_length:
        issues.append(_e("length", f"{n} characters, limit is {max_length}"))
    if max_line_length is not None:
        for i, line in enumerate(translation.split("\n"), start=1):
            if len(line) > max_line_length:
                issues.append(_e("line_length", f"line {i}: {len(line)} characters, limit is {max_line_length}"))
    if max_length is None and len(source) >= 10 and n > LENGTH_RATIO_WARNING * len(source):
        issues.append(_i("length_ratio", f"translation is {n / len(source):.1f}× the English length "
                                         f"(no limit known yet)"))
    return issues


# ------------------------------------------------------------------ unicode / arabic script
def check_unicode(translation: str) -> list[Issue]:
    issues = []
    t = translation
    if "�" in t:
        issues.append(_e("unicode", "contains U+FFFD (broken encoding)"))
    if ar.CONTROL_CHARS.search(t):
        issues.append(_e("unicode", "contains invisible control characters"))
    if ar.PRESENTATION_FORMS.search(t):
        issues.append(_e("unicode", "contains pre-shaped presentation forms; store normal Arabic letters, "
                                    "shaping happens at build time"))
    for ch, name in ar.NON_ARABIC_LOOKALIKES.items():
        if ch in t:
            issues.append(_e("unicode", f"non-Arabic look-alike letter, use {name}"))
    if ar.BIDI_CONTROLS.search(t):
        issues.append(_w("unicode", "contains invisible direction marks (LRM/RLM/embedding)"))
    if ar.ZERO_WIDTH.search(t):
        issues.append(_w("unicode", "contains zero-width characters"))
    if ar.TATWEEL in t:
        issues.append(_w("style", "contains tatweel (ـ)"))
    if unicodedata.normalize("NFC", t) != t:
        issues.append(_w("unicode", "text is not in Unicode NFC form"))
    plain = strip_tokens(t)
    if ar.LATIN_LETTERS.search(plain):
        latin = sorted({w for w in plain.split() if ar.LATIN_LETTERS.search(w)})
        issues.append(_w("latin", "Latin letters outside codes: " + " ".join(latin[:5])))
    if ar.ARABIC_INDIC_DIGITS.search(plain):
        issues.append(_i("digits", "uses Arabic-Indic digits (project default is 0-9, decision open)"))
    if ar.has_arabic(plain):
        found = [c for c in LATIN_PUNCT if c in plain]
        if found:
            issues.append(_w("punctuation", "Latin punctuation in Arabic text: " +
                             " ".join(f"{c}→{LATIN_PUNCT[c]}" for c in found)))
    if "  " in t:
        issues.append(_i("style", "double space"))
    return issues


# ------------------------------------------------------------------ font coverage
def check_font_coverage(translation: str, cmap: set[int]) -> list[Issue]:
    """cmap = set of codepoints in the font. Checks logical AND shaped characters."""
    issues = []
    logical = {c for c in translation if c not in "\n\r\t"}
    missing_logical = sorted(c for c in logical if ord(c) not in cmap and not c.isspace())
    try:
        shaped = set(ar.shape_for_display(translation))
    except Exception as exc:  # pragma: no cover
        return [_w("font", f"could not shape text: {exc}")]
    missing_shaped = sorted(c for c in shaped - logical if ord(c) not in cmap and not c.isspace())
    if missing_logical:
        issues.append(_e("font", "font lacks characters: " +
                         " ".join(f"U+{ord(c):04X}" for c in missing_logical[:10])))
    if missing_shaped:
        issues.append(_w("font", "font lacks shaped forms (needed if the game cannot shape Arabic itself): " +
                         " ".join(f"U+{ord(c):04X}" for c in missing_shaped[:10])))
    return issues


def check_string(source: str, translation, status: str, max_length=None, max_line_length=None,
                 terms=None, cmap=None) -> list[Issue]:
    """Run every single-string check."""
    issues = check_empty(source, translation, status)
    if not translation or not translation.strip():
        return issues
    issues += check_placeholders(source or "", translation)
    if terms:
        issues += check_glossary(source or "", translation, terms)
    issues += check_length(source or "", translation, max_length, max_line_length)
    issues += check_unicode(translation)
    if cmap is not None:
        issues += check_font_coverage(translation, cmap)
    return issues
