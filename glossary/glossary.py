"""Glossary: load from CSV into the DB, detect terms in English text, check Arabic usage.

glossary.csv is the editable source of truth (easy to review in Excel / VS Code).
`python cli.py glossary-load` copies it into the DB table used by the validator.

Columns
  term_en        English term as it appears in the source (matching ignores case by default)
  aliases_en     other English forms to detect, separated by |   (plural, possessive...)
  approved_ar    the approved Arabic translation
  variants_ar    other accepted Arabic forms, separated by |     (inflections, synonyms)
  forbidden_ar   translations that must not be used, separated by |
  category       concept / place / faction / item / name / ui ...
  context        where / how the term is used
  notes          free notes, reasons for the choice
  status         proposed | approved   (approved terms are enforced as errors, proposed as warnings)
  case_sensitive 1 if the English term should only match with exact case
"""
import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from common.arabic import definite_forms, normalize_for_match
from common.paths import GLOSSARY_CSV

COLUMNS = ["term_en", "aliases_en", "approved_ar", "variants_ar", "forbidden_ar",
           "category", "context", "notes", "status", "case_sensitive"]


def _split(value) -> list[str]:
    return [v.strip() for v in (value or "").split("|") if v.strip()]


@dataclass
class Term:
    term_en: str
    approved_ar: str
    aliases_en: list[str] = field(default_factory=list)
    variants_ar: list[str] = field(default_factory=list)
    forbidden_ar: list[str] = field(default_factory=list)
    category: str = ""
    context: str = ""
    notes: str = ""
    status: str = "proposed"
    case_sensitive: bool = False
    female_ar: str = ""                                   # form used in female versions (optional)
    female_variants_ar: list[str] = field(default_factory=list)

    def __post_init__(self):
        flags = 0 if self.case_sensitive else re.IGNORECASE
        forms = sorted({self.term_en, *self.aliases_en}, key=len, reverse=True)
        # word boundaries that also work with apostrophes ("Hunter's")
        self._pattern = re.compile(
            r"(?<![A-Za-z])(?:" + "|".join(re.escape(f) for f in forms) + r")(?![A-Za-z])", flags)
        self._accepted = set()
        for ar in [self.approved_ar, *self.variants_ar]:
            self._accepted.update(definite_forms(ar))
        self._forbidden = [normalize_for_match(f) for f in self.forbidden_ar]
        if self.female_ar:
            self._accepted_female = set()
            for ar in [self.female_ar, *self.female_variants_ar]:
                self._accepted_female.update(definite_forms(ar))
        else:
            self._accepted_female = self._accepted          # no female form defined: same rule

    def occurs_in(self, english: str) -> bool:
        return bool(english and self._pattern.search(english))

    def count_in(self, english: str) -> int:
        return len(self._pattern.findall(english or ""))

    def used_in(self, arabic: str, gender: str = "shared") -> bool:
        norm = normalize_for_match(arabic)
        accepted = self._accepted_female if gender == "female" else self._accepted
        return any(form in norm for form in accepted)

    def expected(self, gender: str = "shared") -> tuple[str, list[str]]:
        if gender == "female" and self.female_ar:
            return self.female_ar, self.female_variants_ar
        return self.approved_ar, self.variants_ar

    def forbidden_used(self, arabic: str) -> list[str]:
        norm = normalize_for_match(arabic)
        return [orig for orig, f in zip(self.forbidden_ar, self._forbidden) if f and f in norm]


# ---------------------------------------------------------------- CSV / DB
def read_csv(path: Path | str = GLOSSARY_CSV) -> list[Term]:
    terms = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for i, rec in enumerate(csv.DictReader(f), start=2):
            if not (rec.get("term_en") or "").strip() or (rec.get("term_en") or "").startswith("#"):
                continue
            if not (rec.get("approved_ar") or "").strip():
                raise ValueError(f"{path}: line {i}: '{rec['term_en']}' has no approved_ar")
            status = (rec.get("status") or "proposed").strip().lower()
            if status not in ("proposed", "approved"):
                raise ValueError(f"{path}: line {i}: status must be proposed/approved, got {status!r}")
            terms.append(Term(
                term_en=rec["term_en"].strip(),
                approved_ar=rec["approved_ar"].strip(),
                aliases_en=_split(rec.get("aliases_en")),
                variants_ar=_split(rec.get("variants_ar")),
                forbidden_ar=_split(rec.get("forbidden_ar")),
                category=(rec.get("category") or "").strip(),
                context=(rec.get("context") or "").strip(),
                notes=(rec.get("notes") or "").strip(),
                status=status,
                case_sensitive=(rec.get("case_sensitive") or "0").strip() in ("1", "true", "yes"),
                female_ar=(rec.get("female_ar") or "").strip(),             # optional columns
                female_variants_ar=_split(rec.get("female_variants_ar")),
            ))
    seen = {}
    for t in terms:
        k = t.term_en.lower()
        if k in seen:
            raise ValueError(f"duplicate glossary term: {t.term_en}")
        seen[k] = t
    return terms


def load_into_db(conn, path: Path | str = GLOSSARY_CSV) -> int:
    """Replace the DB glossary with the CSV content (CSV is the source of truth)."""
    terms = read_csv(path)
    conn.execute("DELETE FROM glossary")
    for t in terms:
        conn.execute(
            "INSERT INTO glossary(term_en, aliases_en, approved_ar, variants_ar, forbidden_ar,"
            " category, context, notes, status, case_sensitive, female_ar, female_variants_ar)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (t.term_en, "|".join(t.aliases_en), t.approved_ar, "|".join(t.variants_ar),
             "|".join(t.forbidden_ar), t.category, t.context, t.notes, t.status, int(t.case_sensitive),
             t.female_ar or None, "|".join(t.female_variants_ar) or None),
        )
    return len(terms)


def from_db(conn) -> list[Term]:
    return [Term(term_en=r["term_en"], approved_ar=r["approved_ar"],
                 aliases_en=_split(r["aliases_en"]), variants_ar=_split(r["variants_ar"]),
                 forbidden_ar=_split(r["forbidden_ar"]), category=r["category"] or "",
                 context=r["context"] or "", notes=r["notes"] or "", status=r["status"],
                 case_sensitive=bool(r["case_sensitive"]),
                 female_ar=(r["female_ar"] or "") if "female_ar" in r.keys() else "",
                 female_variants_ar=_split(r["female_variants_ar"]) if "female_variants_ar" in r.keys() else [])
            for r in conn.execute("SELECT * FROM glossary ORDER BY term_en")]


def terms_in(english: str, terms: list[Term]) -> list[Term]:
    """Glossary terms that appear in an English string.

    If a longer term contains a shorter one ("Hunter's Dream" vs "Hunter") and both
    match, the shorter one is dropped when it only occurs inside the longer one.
    """
    found = [t for t in terms if t.occurs_in(english)]
    result = []
    for t in found:
        covered = 0
        for other in found:
            if other is not t and len(other.term_en) > len(t.term_en) and t.occurs_in(other.term_en):
                covered += other.count_in(english)
        if t.count_in(english) > covered:
            result.append(t)
    return result
