"""Validate all strings in the DB, including cross-string checks, and write reports."""
import csv
import datetime as dt
from collections import defaultdict
from pathlib import Path

from common.paths import LOGS_DIR, ensure_dir
import unicodedata
from collections import Counter

from common.arabic import BIDI_CONTROLS, HARAKAT, ZERO_WIDTH
from common.placeholders import find_tokens
from glossary.glossary import from_db
from validation.checks import Issue, check_string


def load_cmap(font_path) -> set[int]:
    from fontTools.ttLib import TTFont
    with TTFont(str(font_path), fontNumber=0, lazy=True) as font:
        return set(font.getBestCmap() or {})


def duplicate_issues(rows) -> dict[int, list[Issue]]:
    """Same English source translated differently / same Arabic used for different sources."""
    by_source = defaultdict(list)
    by_translation = defaultdict(list)
    for r in rows:
        if "gender" in r.keys() and r["gender"] == "female":
            continue                    # a male/female pair is not a "duplicate"; pairs are checked below
        if r["translation"] and r["translation"].strip():
            by_source[r["source_text"].strip()].append(r)
            by_translation[r["translation"].strip()].append(r)
    out = defaultdict(list)
    for src, group in by_source.items():
        variants = {r["translation"].strip() for r in group}
        if len(group) > 1 and len(variants) > 1:
            keys = ", ".join(r["string_key"] for r in group)
            for r in group:
                out[r["id"]].append(Issue("warning", "duplicate_source",
                                          f"same English text translated {len(variants)} different ways ({keys})"))
    for trg, group in by_translation.items():
        sources = {r["source_text"].strip() for r in group}
        if len(sources) > 1:
            keys = ", ".join(r["string_key"] for r in group)
            for r in group:
                out[r["id"]].append(Issue("info", "duplicate_translation",
                                          f"same Arabic text used for different English texts ({keys})"))
    return out


def _practical(text: str) -> str:
    """Text as a reader sees it: NFC, no invisible marks, single spaces (diacritics kept)."""
    t = unicodedata.normalize("NFC", text or "")
    t = ZERO_WIDTH.sub("", BIDI_CONTROLS.sub("", t))
    return " ".join(t.split())


def pair_issues(rows) -> dict[int, list[Issue]]:
    """Checks between the male and female versions of the same string_key.

    rows must contain ALL versions of the keys to check.
      * a male version without a female one (or the reverse)        -> error
      * different placeholders/codes in the two versions             -> error
      * identical texts (after removing invisible differences)       -> warning (merging them waits for schema v3)
      * texts that differ only in diacritics                         -> info; allowed, never merged
    """
    by_key = defaultdict(dict)
    for r in rows:
        if "gender" in r.keys() and r["gender"] in ("male", "female"):
            by_key[r["string_key"]][r["gender"]] = r
    out = defaultdict(list)
    for key, v in by_key.items():
        m, f = v.get("male"), v.get("female")
        if m is None or f is None:
            have = m or f
            out[have["id"]].append(Issue("error", "variant_missing",
                                         f"{have['gender']} version without a "
                                         f"{'female' if f is None else 'male'} version"))
            continue
        mt, ft = m["translation"] or "", f["translation"] or ""
        if Counter(find_tokens(mt)) != Counter(find_tokens(ft)):
            for r in (m, f):
                out[r["id"]].append(Issue("error", "variant_placeholders",
                                          "male and female versions use different codes/placeholders"))
        pm, pf = _practical(mt), _practical(ft)
        if pm and pm == pf:
            for r in (m, f):
                out[r["id"]].append(Issue("warning", "variants_identical",
                                          "male and female versions are identical (merging them back is disabled until schema v3)"))
        elif pm and HARAKAT.sub("", pm) == HARAKAT.sub("", pf):
            for r in (m, f):
                out[r["id"]].append(Issue("info", "variants_diacritics_only",
                                          "male and female versions differ only in diacritics (allowed)"))
    return out


def validate_row(row, terms, cmap=None) -> list[Issue]:
    gender = row["gender"] if "gender" in row.keys() else "shared"
    category = row["category"] if "category" in row.keys() else None
    return check_string(row["source_text"], row["translation"], row["status"],
                        row["max_length"], row["max_line_length"], terms, cmap, gender=gender, category=category)


def errors_for(conn, string_key: str, font_path=None, gender=None) -> list[str]:
    """Blocking errors for one version of a string (used before approving)."""
    from database import db
    row = db.resolve(conn, string_key, gender)
    cmap = load_cmap(font_path) if font_path else None
    issues = validate_row(row, from_db(conn), cmap)
    issues += pair_issues(db.versions(conn, string_key)).get(row["id"], [])
    return [i.message for i in issues if i.severity == "error"]


def run(conn, font_path=None, statuses=None, report_dir: Path | None = None) -> dict:
    terms = from_db(conn)
    cmap = load_cmap(font_path) if font_path else None
    sql = "SELECT * FROM strings"
    params = []
    if statuses:
        sql += f" WHERE status IN ({','.join('?' * len(statuses))})"
        params = list(statuses)
    rows = conn.execute(sql + " ORDER BY string_key", params).fetchall()

    per_row = {r["id"]: validate_row(r, terms, cmap) for r in rows}
    for rid, extra in duplicate_issues(rows).items():
        per_row.setdefault(rid, []).extend(extra)
    keys = {r["string_key"] for r in rows}
    all_versions = [r for r in conn.execute("SELECT * FROM strings WHERE gender IN ('male','female')")
                    if r["string_key"] in keys]
    selected = {r["id"] for r in rows}
    for rid, extra in pair_issues(all_versions).items():
        if rid in selected:
            per_row.setdefault(rid, []).extend(extra)

    if statuses:
        # partial run: replace only the results of the rows checked now, keep everyone else's
        conn.executemany("DELETE FROM validation_issues WHERE string_id = ?", [(rid,) for rid in selected])
    else:
        conn.execute("DELETE FROM validation_issues")
    report = []
    for r in rows:
        for issue in per_row.get(r["id"], []):
            conn.execute("INSERT INTO validation_issues(string_id, severity, check_name, message)"
                         " VALUES (?,?,?,?)", (r["id"], issue.severity, issue.check, issue.message))
            report.append((r["string_key"], r["gender"], r["status"], issue.severity, issue.check,
                           issue.message, r["source_text"], r["translation"] or ""))

    summary = {"strings": len(rows), "error": 0, "warning": 0, "info": 0,
               "strings_with_errors": 0, "glossary_terms": len(terms), "font_checked": bool(cmap)}
    for _, _, _, sev, *_ in report:
        summary[sev] += 1
    summary["strings_with_errors"] = sum(
        1 for issues in per_row.values() if any(i.severity == "error" for i in issues))

    report_dir = report_dir or LOGS_DIR          # resolved at call time (tests point LOGS_DIR elsewhere)
    ensure_dir(report_dir)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = Path(report_dir) / f"validation_{stamp}.csv"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["string_key", "gender", "status", "severity", "check", "message", "source_text",
                    "translation"])
        order = {"error": 0, "warning": 1, "info": 2}
        for line in sorted(report, key=lambda x: (order[x[3]], x[0], x[1])):
            w.writerow(line)
    summary["report"] = str(csv_path)
    return summary
