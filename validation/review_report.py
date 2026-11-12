"""Review sheet for the human reviewer (read-only).

One CSV row per version (shared / male / female) of the selected strings, with the English source,
the Arabic, the other gender version, the codes, the glossary terms and the current errors and
warnings side by side. The sheet is for reading only: nothing in it is ever imported, and approving
stays one string at a time with 'cli.py approve'.
"""
import csv
import datetime as dt
from pathlib import Path

from common.paths import LOGS_DIR, ensure_dir
from common.placeholders import find_tokens
from glossary.glossary import from_db, terms_in
from validation.checks import check_string
from validation.validator import pair_issues

REVIEW_COLUMNS = ["string_key", "gender", "status", "type", "speaker", "context", "max_length", "source",
                  "translation", "other_version", "placeholders", "glossary", "errors", "warnings",
                  "origin", "translator", "updated_at"]


def export_review(conn, out_path=None, statuses=("needs_review",), report_dir: Path | None = None) -> dict:
    """Write the review sheet. Reads the database only."""
    statuses = tuple(statuses) or ("needs_review",)
    terms = from_db(conn)
    keys = [r[0] for r in conn.execute(
        "SELECT DISTINCT string_key FROM strings WHERE status IN (%s) ORDER BY string_key"
        % ",".join("?" * len(statuses)), statuses)]
    lines = []
    for key in keys:
        versions = conn.execute("SELECT * FROM strings WHERE string_key=? ORDER BY CASE gender "
                                "WHEN 'shared' THEN 0 WHEN 'male' THEN 1 ELSE 2 END", (key,)).fetchall()
        pairs = pair_issues(versions) if len(versions) > 1 else {}
        by_gender = {v["gender"]: v for v in versions}
        for v in versions:
            if v["status"] not in statuses:
                continue
            issues = []
            if v["translation"]:
                issues = check_string(v["source_text"], v["translation"], v["status"], v["max_length"],
                                      v["max_line_length"], terms, gender=v["gender"], category=v["category"])
            issues += pairs.get(v["id"], [])
            other = {"male": "female", "female": "male"}.get(v["gender"])
            gl = "; ".join(f"{t.term_en} = {t.approved_ar}" + (f" (female: {t.female_ar})" if t.female_ar else "")
                           for t in terms_in(v["source_text"], terms))
            lines.append([v["string_key"], v["gender"], v["status"], v["category"] or "", v["speaker"] or "",
                          v["context"] or "", "" if v["max_length"] is None else v["max_length"],
                          v["source_text"], v["translation"] or "",
                          (by_gender[other]["translation"] or "") if other in by_gender else "",
                          " | ".join(find_tokens(v["source_text"])), gl,
                          " | ".join(f"{i.check}: {i.message}" for i in issues if i.severity == "error"),
                          " | ".join(f"{i.check}: {i.message}" for i in issues if i.severity == "warning"),
                          v["origin"], v["translator"] or "", v["updated_at"]])
    if out_path is None:
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = Path(report_dir or LOGS_DIR) / f"review_{stamp}.csv"
    out_path = Path(out_path)
    ensure_dir(out_path.parent)
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(REVIEW_COLUMNS)
        w.writerows(lines)
    return {"rows": len(lines), "strings": len(keys), "statuses": list(statuses), "file": str(out_path),
            "with_errors": sum(1 for x in lines if x[12])}
