"""Batch translation via CSV.

  1. import_sources()  English strings  ->  DB (status pending)
  2. export_batch()    pending strings  ->  translation/batches/out/<batch_id>.csv
  3. (translator, e.g. Claude, fills the 'translation' column, see CLAUDE_TRANSLATOR.md)
  4. import_batch()    returned CSV     ->  DB
       * every row is checked against the batch record and the source fingerprint
       * translation stored as machine output
       * validator errors  -> stays 'draft'
       * no errors         -> 'needs_review'   (warnings do not block review)
       * nothing is ever approved here
"""
import csv
import datetime as dt
import hashlib
import io
import re
from pathlib import Path

from common.arabic import has_arabic
from common.paths import GLOSSARY_CSV, LOGS_DIR, TRANSLATION_DIR, ensure_dir
from common.placeholders import find_tokens
from database import db
from glossary.glossary import from_db, read_csv as read_glossary, terms_in
from validation.checks import Issue, check_string
from validation.validator import duplicate_issues

BATCH_OUT = TRANSLATION_DIR / "batches" / "out"
BATCH_IN = TRANSLATION_DIR / "batches" / "in"
DEFAULT_BATCH_SIZE = 40
MACHINE_MODEL = "claude"

ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:/\-]+$")
TYPES = {
    "dialogue": "dialogue", "حوار": "dialogue",
    "ui": "ui", "واجهة": "ui",
    "item_name": "item_name", "اسم عنصر": "item_name",
    "item_desc": "item_desc", "وصف عنصر": "item_desc",
    "system": "system", "رسالة نظام": "system",
    "place": "place", "مكان": "place",
}
BATCH_COLUMNS = ["batch_id", "id", "type", "speaker", "context", "max_length", "source",
                 "placeholders", "glossary", "source_hash", "translation", "translator_note"]


class BatchError(Exception):
    """The whole file is refused; nothing was written to the DB."""

    def __init__(self, message, problems=None):
        super().__init__(message)
        self.problems = problems or []


# ------------------------------------------------------------------ helpers
def source_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _read_csv_strict(path) -> list[dict]:
    """Read a CSV as UTF-8 (with or without BOM). Anything else is refused."""
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BatchError(f"{path}: file is not UTF-8 (byte {exc.start}). If you saved it with Excel, "
                         f"use 'CSV UTF-8 (Comma delimited)'.")
    return list(csv.DictReader(io.StringIO(text, newline="")))


def _norm_cols(rows, aliases: dict) -> list[dict]:
    out = []
    for r in rows:
        n = {}
        for k, v in r.items():
            if k is None:
                continue
            k = k.strip().lower()
            n[aliases.get(k, k)] = v
        out.append(n)
    return out


def _terms(conn):
    terms = from_db(conn)
    return terms if terms else read_glossary(GLOSSARY_CSV)


def _stamp():
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _write_report(path: Path, header, rows):
    ensure_dir(path.parent)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return str(path)


# ------------------------------------------------------------------ 1. sources
def import_sources(conn, csv_path, report_dir: Path | None = None) -> dict:
    """Import English source strings: columns id, source, context, type (+ speaker, max_length).

    The whole file is refused (nothing written) if any id is duplicated or invalid,
    a type is unknown, or a source is empty.
    """
    rows = _norm_cols(_read_csv_strict(csv_path),
                      {"string_key": "id", "source_text": "source", "category": "type"})
    if rows and not {"id", "source"} <= set(rows[0]):
        raise BatchError("CSV needs at least the columns: id, source (optional: context, type, speaker, max_length)")

    problems, seen = [], {}
    for line, r in enumerate(rows, start=2):
        rid = (r.get("id") or "").strip()
        if not rid or not ID_PATTERN.match(rid):
            problems.append(f"line {line}: invalid id {rid!r} (allowed: A-Z a-z 0-9 _ . : / -)")
        elif rid in seen:
            problems.append(f"line {line}: duplicate id {rid!r} (first on line {seen[rid]})")
        else:
            seen[rid] = line
        if not (r.get("source") or "").strip():
            problems.append(f"line {line}: empty source for id {rid!r}")
        t = (r.get("type") or "").strip()
        if t and t.lower() not in TYPES and t not in TYPES:
            problems.append(f"line {line}: unknown type {t!r} (use: {', '.join(sorted(set(TYPES.values())))})")
        ml = (r.get("max_length") or "").strip()
        if ml and not ml.isdigit():
            problems.append(f"line {line}: max_length must be a number, got {ml!r}")
    if problems:
        raise BatchError(f"{csv_path}: file refused, nothing imported", problems)

    stats = {"inserted": 0, "updated": 0, "source_changed": 0, "unchanged": 0, "warnings": 0}
    report = []
    by_source = {}
    for r in rows:
        rid = r["id"].strip()
        src = r["source"]
        t = (r.get("type") or "").strip()
        before = conn.execute("SELECT status, translation FROM strings WHERE string_key=?", (rid,)).fetchone()
        result = db.upsert_source(conn, rid, src,
                                  category=TYPES.get(t.lower(), TYPES.get(t)) if t else None,
                                  context=r.get("context"), speaker=r.get("speaker"),
                                  max_length=r.get("max_length"))
        stats[result] += 1
        if result == "source_changed":
            after = db.get_string(conn, rid)
            msg = "English source changed for an existing id"
            if before and before["translation"]:
                msg += f"; existing translation kept, status {before['status']} -> {after['status']}"
            report.append((rid, "warning", "source_changed", msg))
        by_source.setdefault(src.strip(), []).append(rid)

    # same English text under different ids (inside the file or already in the DB)
    for src, ids in by_source.items():
        db_ids = [r["string_key"] for r in conn.execute(
            "SELECT string_key FROM strings WHERE trim(source_text)=?", (src,))]
        all_ids = sorted(set(ids) | set(db_ids))
        if len(all_ids) > 1:
            for rid in ids:
                report.append((rid, "warning", "duplicate_source",
                               "same English text under ids: " + ", ".join(all_ids)))
    stats["warnings"] = len(report)
    stats["report"] = _write_report(Path(report_dir or LOGS_DIR) / f"sources_import_{_stamp()}.csv",
                                    ["id", "severity", "check", "message"], report)
    return stats


# ------------------------------------------------------------------ 2. export
def export_batch(conn, limit: int = DEFAULT_BATCH_SIZE, types=None, statuses=("pending",),
                 include_open: bool = False, out_dir: Path | None = None) -> dict:
    """Write the next batch of strings for the translator. Rows already in an
    exported-but-not-imported batch are skipped unless include_open=True."""
    sql = "SELECT * FROM strings WHERE status IN (%s)" % ",".join("?" * len(statuses))
    params = list(statuses)
    if types:
        types = [TYPES.get(t, t) for t in types]
        sql += " AND category IN (%s)" % ",".join("?" * len(types))
        params += types
    if not include_open:
        sql += (" AND id NOT IN (SELECT bi.string_id FROM batch_items bi JOIN batches b"
                " ON b.batch_id = bi.batch_id WHERE bi.result IS NULL)")
    rows = conn.execute(sql + " ORDER BY string_key LIMIT ?", params + [limit]).fetchall()
    if not rows:
        return {"batch_id": None, "rows": 0, "file": None}

    batch_id = f"batch_{_stamp()}"
    n = 1
    while conn.execute("SELECT 1 FROM batches WHERE batch_id=?", (batch_id,)).fetchone():
        n += 1
        batch_id = f"batch_{_stamp()}_{n}"
    out_path = Path(out_dir or BATCH_OUT) / f"{batch_id}.csv"
    if out_dir is None:
        ensure_dir(BATCH_IN)   # where the translated file is saved back
    terms = _terms(conn)

    conn.execute("INSERT INTO batches(batch_id, file_path, row_count) VALUES (?,?,?)",
                 (batch_id, str(out_path), len(rows)))
    lines = []
    for r in rows:
        h = source_hash(r["source_text"])
        conn.execute("INSERT INTO batch_items(batch_id, string_id, string_key, source_hash) VALUES (?,?,?,?)",
                     (batch_id, r["id"], r["string_key"], h))
        gl = "; ".join(f"{t.term_en} = {t.approved_ar}" for t in terms_in(r["source_text"], terms))
        lines.append([batch_id, r["string_key"], r["category"] or "", r["speaker"] or "", r["context"] or "",
                      "" if r["max_length"] is None else r["max_length"], r["source_text"],
                      " | ".join(find_tokens(r["source_text"])), gl, h, "", ""])
    ensure_dir(out_path.parent)
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(BATCH_COLUMNS)
        w.writerows(lines)
    return {"batch_id": batch_id, "rows": len(rows), "file": str(out_path)}


# ------------------------------------------------------------------ 3. import
def _looks_like_lost_encoding(text: str) -> bool:
    """Arabic replaced by '?' or U+FFFD (typical after saving in a non-UTF-8 encoding)."""
    if "�" in text:
        return True
    if has_arabic(text):
        return False
    visible = [c for c in text if not c.isspace()]
    q = text.count("?")
    return q >= 3 and q >= len(visible) / 2


def import_batch(conn, csv_path, translator: str = MACHINE_MODEL, allow_overwrite_approved: bool = False,
                 overwrite_drafts: bool = False, report_dir: Path | None = None) -> dict:
    rows = _norm_cols(_read_csv_strict(csv_path), {"string_key": "id"})
    needed = {"batch_id", "id", "source", "source_hash", "translation"}
    if not rows:
        raise BatchError(f"{csv_path}: file has no rows")
    missing = needed - set(rows[0])
    if missing:
        raise BatchError(f"{csv_path}: missing columns: {', '.join(sorted(missing))}")

    # ---- whole-file checks (nothing written if any fails)
    problems = []
    batch_ids = {(r.get("batch_id") or "").strip() for r in rows}
    if len(batch_ids) != 1:
        problems.append(f"rows belong to several batches: {sorted(batch_ids)}")
    batch_id = next(iter(batch_ids))
    batch = conn.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
    if batch is None:
        problems.append(f"unknown batch_id {batch_id!r} (was it exported from this database?)")
    seen = {}
    for line, r in enumerate(rows, start=2):
        rid = (r.get("id") or "").strip()
        if rid in seen:
            problems.append(f"line {line}: duplicate id {rid!r} (first on line {seen[rid]})")
        seen.setdefault(rid, line)
        tr = r.get("translation") or ""
        if tr.strip() and _looks_like_lost_encoding(tr):
            problems.append(f"line {line}: translation of {rid!r} looks like lost Arabic encoding: {tr[:30]!r}")
    if problems:
        raise BatchError(f"{csv_path}: file refused, nothing imported", problems)

    items = {r["string_key"]: r for r in conn.execute("SELECT * FROM batch_items WHERE batch_id=?", (batch_id,))}
    terms = _terms(conn)
    report, results = [], {}
    stats = {"accepted_review": 0, "accepted_draft": 0, "unchanged": 0, "rejected": 0, "skipped": 0,
             "errors": 0, "warnings": 0}

    def reject(rid, reason, msg, item=None):
        stats["rejected"] += 1
        report.append((rid, "rejected", "", "error", reason, msg, "", ""))
        if item is not None:
            results[item["string_id"]] = "rejected"

    for r in rows:
        rid = r["id"].strip()
        tr = r.get("translation") or ""
        note = (r.get("translator_note") or "").strip() or None
        current = conn.execute("SELECT * FROM strings WHERE string_key=?", (rid,)).fetchone()
        if current is None:
            reject(rid, "unknown_id", "id does not exist in the database")
            continue
        item = items.get(rid)
        if item is None:
            reject(rid, "not_in_batch", f"id was not exported in {batch_id}")
            continue
        # fingerprints: file source vs export, DB source vs export
        if source_hash(r.get("source") or "") != item["source_hash"] or \
                (r.get("source_hash") or "").strip() != item["source_hash"]:
            reject(rid, "modified_source", "the 'source' or 'source_hash' column was changed after export", item)
            continue
        if source_hash(current["source_text"]) != item["source_hash"]:
            reject(rid, "stale", "English source changed in the DB since export; export a new batch", item)
            continue
        if not tr.strip():
            stats["skipped"] += 1
            results[item["string_id"]] = "skipped"
            report.append((rid, "skipped", current["status"], "info", "empty", "no translation in file", "", ""))
            continue
        if tr == current["translation"]:
            stats["unchanged"] += 1
            results[item["string_id"]] = "unchanged"
            report.append((rid, "unchanged", current["status"], "info", "unchanged",
                           "translation identical to the database", r["source"], tr))
            continue
        if current["status"] == "approved" and not allow_overwrite_approved:
            reject(rid, "approved_protected",
                   "row is approved; use --allow-overwrite-approved to replace it (it would go back to review)", item)
            continue
        if current["status"] in ("draft", "needs_review") and current["origin"] == "human" \
                and current["translation"] and not overwrite_drafts:
            reject(rid, "human_draft_protected",
                   f"row has a human translation ({current['status']}); use --overwrite-drafts to replace it", item)
            continue

        overwrite_note = None
        if current["translation"]:
            overwrite_note = f"replaced previous {current['status']} ({current['origin']}) translation"
        db.set_translation(conn, rid, tr, "machine", translator, machine_model=MACHINE_MODEL,
                           note="; ".join(x for x in (f"batch {batch_id}", overwrite_note, note) if x))
        issues = check_string(current["source_text"], tr, "draft", current["max_length"],
                              current["max_line_length"], terms)
        errors = [i for i in issues if i.severity == "error"]
        if errors:
            outcome = "accepted_draft"
        else:
            db.mark_for_review(conn, rid, "batch-import")
            outcome = "accepted_review"
        stats[outcome] += 1
        results[item["string_id"]] = outcome
        after = db.get_string(conn, rid)
        conn.execute("DELETE FROM validation_issues WHERE string_id=?", (after["id"],))
        for i in issues:
            conn.execute("INSERT INTO validation_issues(string_id, severity, check_name, message) VALUES (?,?,?,?)",
                         (after["id"], i.severity, i.check, i.message))
        if overwrite_note:
            issues = [Issue("warning", "overwrite", overwrite_note)] + issues
        if note:
            issues.append(Issue("info", "translator_note", note))
        if not issues:
            report.append((rid, outcome, after["status"], "", "", "", r["source"], tr))
        for i in issues:
            if i.severity == "error":
                stats["errors"] += 1
            elif i.severity == "warning":
                stats["warnings"] += 1
            report.append((rid, outcome, after["status"], i.severity, i.check, i.message, r["source"], tr))

    # consistency warnings against the whole DB (same English, different Arabic)
    all_rows = conn.execute("SELECT * FROM strings").fetchall()
    batch_string_ids = {i["string_id"] for i in items.values()}
    for sid, issues in duplicate_issues(all_rows).items():
        if sid in batch_string_ids and results.get(sid) in ("accepted_review", "accepted_draft"):
            key = conn.execute("SELECT string_key, status FROM strings WHERE id=?", (sid,)).fetchone()
            for i in issues:
                if i.severity == "warning":
                    stats["warnings"] += 1
                report.append((key["string_key"], results[sid], key["status"], i.severity, i.check, i.message, "", ""))

    for sid, res in results.items():
        conn.execute("UPDATE batch_items SET result=? WHERE batch_id=? AND string_id=?", (res, batch_id, sid))
    open_left = conn.execute("SELECT COUNT(*) FROM batch_items WHERE batch_id=? AND (result IS NULL OR result IN"
                             " ('rejected','skipped','accepted_draft'))", (batch_id,)).fetchone()[0]
    conn.execute("UPDATE batches SET status=?, imported_at=datetime('now') WHERE batch_id=?",
                 ("imported" if open_left == 0 else "partially_imported", batch_id))

    stats["batch_id"] = batch_id
    stats["approved_by_import"] = 0
    order = {"error": 0, "warning": 1, "info": 2, "": 3}
    report.sort(key=lambda x: (order[x[3]], x[0]))
    stats["report"] = _write_report(Path(report_dir or LOGS_DIR) / f"{batch_id}_report.csv",
                                    ["id", "result", "status_after", "severity", "check", "message",
                                     "source", "translation"], report)
    return stats


def list_batches(conn) -> list[dict]:
    out = []
    for b in conn.execute("SELECT * FROM batches ORDER BY created_at, batch_id"):
        counts = {r["result"] or "waiting": r["n"] for r in conn.execute(
            "SELECT result, COUNT(*) n FROM batch_items WHERE batch_id=? GROUP BY result", (b["batch_id"],))}
        out.append({"batch_id": b["batch_id"], "status": b["status"], "rows": b["row_count"],
                    "created_at": b["created_at"], "imported_at": b["imported_at"], "results": counts})
    return out
