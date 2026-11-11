"""Validate all strings in the DB, including cross-string checks, and write reports."""
import csv
import datetime as dt
from collections import defaultdict
from pathlib import Path

from common.paths import LOGS_DIR, ensure_dir
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


def validate_row(row, terms, cmap=None) -> list[Issue]:
    return check_string(row["source_text"], row["translation"], row["status"],
                        row["max_length"], row["max_line_length"], terms, cmap)


def errors_for(conn, string_key: str, font_path=None) -> list[str]:
    """Blocking errors for one string (used before approving)."""
    row = conn.execute("SELECT * FROM strings WHERE string_key=?", (string_key,)).fetchone()
    if row is None:
        raise KeyError(string_key)
    cmap = load_cmap(font_path) if font_path else None
    return [i.message for i in validate_row(row, from_db(conn), cmap) if i.severity == "error"]


def run(conn, font_path=None, statuses=None, report_dir: Path = LOGS_DIR) -> dict:
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

    conn.execute("DELETE FROM validation_issues")
    report = []
    for r in rows:
        for issue in per_row.get(r["id"], []):
            conn.execute("INSERT INTO validation_issues(string_id, severity, check_name, message)"
                         " VALUES (?,?,?,?)", (r["id"], issue.severity, issue.check, issue.message))
            report.append((r["string_key"], r["status"], issue.severity, issue.check, issue.message,
                           r["source_text"], r["translation"] or ""))

    summary = {"strings": len(rows), "error": 0, "warning": 0, "info": 0,
               "strings_with_errors": 0, "glossary_terms": len(terms), "font_checked": bool(cmap)}
    for _, _, sev, *_ in report:
        summary[sev] += 1
    summary["strings_with_errors"] = sum(
        1 for issues in per_row.values() if any(i.severity == "error" for i in issues))

    ensure_dir(report_dir)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = Path(report_dir) / f"validation_{stamp}.csv"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["string_key", "status", "severity", "check", "message", "source_text", "translation"])
        order = {"error": 0, "warning": 1, "info": 2}
        for line in sorted(report, key=lambda x: (order[x[2]], x[0])):
            w.writerow(line)
    summary["report"] = str(csv_path)
    return summary
