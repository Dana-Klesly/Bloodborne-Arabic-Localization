"""Batch translation via CSV.

  1. import_sources()  English strings  ->  DB (status pending)
  2. export_batch()    pending strings  ->  translation/batches/out/<batch_id>.csv
  3. (the translator fills the 'translation' column, see TRANSLATOR.md)
  4. import_batch()    returned CSV     ->  DB
       * every row is checked against the batch record and the source fingerprint
       * translation stored as machine output
       * validator errors  -> stays 'draft'
       * no errors         -> 'needs_review'   (warnings do not block review)
       * nothing is ever approved here, and approved versions are never overwritten
  5. cancel_batch()    an exported batch that will not come back: its open rows can be exported again

import_sources() and import_batch() accept dry_run=True: everything is checked and the report is
written, but every database change is rolled back (SAVEPOINT), so the database stays as it was.
"""
import csv
import datetime as dt
import hashlib
import io
import re
from contextlib import contextmanager
from pathlib import Path

from common.arabic import has_arabic
from common.paths import GLOSSARY_CSV, LOGS_DIR, TRANSLATION_DIR, ensure_dir
from common.placeholders import find_tokens
from database import db
from glossary.glossary import from_db, read_csv as read_glossary, terms_in
from validation.checks import Issue, check_string
from validation.validator import duplicate_issues, pair_issues

BATCH_OUT = TRANSLATION_DIR / "batches" / "out"
BATCH_IN = TRANSLATION_DIR / "batches" / "in"
DEFAULT_BATCH_SIZE = 40
MACHINE_MODEL = "ai"

ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:/\-]+$")
TYPES = {
    "dialogue": "dialogue", "حوار": "dialogue",
    "ui": "ui", "واجهة": "ui",
    "item_name": "item_name", "اسم عنصر": "item_name",
    "item_desc": "item_desc", "وصف عنصر": "item_desc",
    "system": "system", "رسالة نظام": "system",
    "place": "place", "مكان": "place",
}
BATCH_COLUMNS = ["batch_id", "id", "type", "gender", "speaker", "context", "max_length", "max_line_length",
                 "source", "placeholders", "glossary", "source_hash", "translation", "translation_female",
                 "translator_note", "current_translation", "current_translation_female", "issues"]
# gender column (read-only, informative): "shared" = one translation for both player genders,
# "male+female" = the string already has two versions; translation = male, translation_female = female.
# current_translation / current_translation_female / issues: read-only information for the translator
# when a string is sent again (e.g. a draft with validator errors). They are never imported.

# Columns that only a human reviewer may set. If a returned batch file contains them they are ignored
# (never read) and listed in the report.
APPROVAL_COLUMNS = {"status", "reviewer", "approved", "approved_by", "approved_at", "approval_id",
                    "signature", "origin"}
CANCELLED = "cancelled"            # batch_items.result of rows taken out of a cancelled batch


class BatchError(Exception):
    """The whole file is refused; nothing was written to the DB."""

    def __init__(self, message, problems=None):
        super().__init__(message)
        self.problems = problems or []


# ------------------------------------------------------------------ helpers
def source_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


@contextmanager
def _dry_run_scope(conn, dry_run: bool):
    """With dry_run, every change made inside the block is rolled back at the end (also on errors)."""
    if not dry_run:
        yield
        return
    outermost = not conn.in_transaction
    conn.execute("SAVEPOINT bbal_dry_run")
    try:
        yield
    finally:
        if outermost:
            conn.rollback()           # end the transaction without a commit: the file is not written at all
        else:
            conn.execute("ROLLBACK TO bbal_dry_run")   # keep the caller's own pending changes
            conn.execute("RELEASE bbal_dry_run")


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


def _version_issues(conn, string_key: str, terms) -> list[str]:
    """Current errors and warnings of every version of a string, as short text lines."""
    out = []
    versions = db.versions(conn, string_key)
    pairs = pair_issues(versions) if len(versions) > 1 else {}
    for v in versions:
        if not v["translation"]:
            continue
        issues = check_string(v["source_text"], v["translation"], v["status"], v["max_length"],
                              v["max_line_length"], terms, gender=v["gender"], category=v["category"])
        issues += pairs.get(v["id"], [])
        prefix = "" if v["gender"] == "shared" else f"{v['gender']}: "
        out += [f"{prefix}{i.severity}:{i.check}: {i.message}" for i in issues
                if i.severity in ("error", "warning")]
    return out


# ------------------------------------------------------------------ 1. sources
SOURCE_INT_COLUMNS = ("max_length", "max_line_length", "entry_id")


def import_sources(conn, csv_path, report_dir: Path | None = None, dry_run: bool = False) -> dict:
    """Import English source strings: columns id, source (+ context, type, speaker, max_length,
    max_line_length, source_file, fmg_name, entry_id).

    The whole file is refused (nothing written) if any id is duplicated or invalid,
    a type is unknown, a number column is not a number, or a source is empty.
    dry_run=True: same checks and report, but nothing stays in the database.
    """
    rows = _norm_cols(_read_csv_strict(csv_path),
                      {"string_key": "id", "source_text": "source", "category": "type"})
    if not rows:
        raise BatchError(f"{csv_path}: file has no rows")
    if not {"id", "source"} <= set(rows[0]):
        raise BatchError("CSV needs at least the columns: id, source (optional: context, type, speaker, "
                         "max_length, max_line_length, source_file, fmg_name, entry_id)")

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
        for col in SOURCE_INT_COLUMNS:
            v = (r.get(col) or "").strip()
            if v and not v.isdigit():
                problems.append(f"line {line}: {col} must be a whole number, got {v!r}")
    if problems:
        raise BatchError(f"{csv_path}: file refused, nothing imported", problems)

    stats = {"inserted": 0, "updated": 0, "source_changed": 0, "unchanged": 0, "warnings": 0,
             "dry_run": dry_run}
    report = []
    with _dry_run_scope(conn, dry_run):
        by_source = {}
        for r in rows:
            rid = r["id"].strip()
            src = r["source"]
            t = (r.get("type") or "").strip()
            before = conn.execute("SELECT status, translation FROM strings WHERE string_key=?", (rid,)).fetchone()
            fields = {"category": TYPES.get(t.lower(), TYPES.get(t)) if t else None,
                      "context": r.get("context"), "speaker": r.get("speaker")}
            for col in SOURCE_INT_COLUMNS + ("source_file", "fmg_name"):
                if col in r:                       # only columns present in the file are touched
                    fields[col] = r.get(col)
            result = db.upsert_source(conn, rid, src, **fields)
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
    name = f"sources_import_{_stamp()}{'_dryrun' if dry_run else ''}.csv"
    stats["report"] = _write_report(Path(report_dir or LOGS_DIR) / name,
                                    ["id", "severity", "check", "message"], report)
    return stats


# ------------------------------------------------------------------ 2. export
def export_batch(conn, limit: int = DEFAULT_BATCH_SIZE, types=None, statuses=("pending",),
                 include_open: bool = False, out_dir: Path | None = None) -> dict:
    """Write the next batch of strings for the translator. Rows already in an
    exported-but-not-imported batch are skipped unless include_open=True.

    Strings that already have a translation (e.g. drafts with validator errors) carry it in the
    read-only columns current_translation / current_translation_female, with their current
    errors and warnings in 'issues', so the translator can fix them.
    """
    if limit is None or int(limit) < 1:
        raise BatchError(f"limit must be at least 1, got {limit!r}")
    sql = ("SELECT * FROM strings b WHERE gender IN ('shared','male') AND EXISTS (SELECT 1 FROM strings v"
           " WHERE v.string_key = b.string_key AND v.status IN (%s))" % ",".join("?" * len(statuses)))
    params = list(statuses)
    if types:
        types = [TYPES.get(t, t) for t in types]
        sql += " AND category IN (%s)" % ",".join("?" * len(types))
        params += types
    if not include_open:
        sql += (" AND id NOT IN (SELECT bi.string_id FROM batch_items bi JOIN batches b"
                " ON b.batch_id = bi.batch_id WHERE bi.result IS NULL)")
    rows = conn.execute(sql + " ORDER BY string_key LIMIT ?", params + [int(limit)]).fetchall()
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
        gl = "; ".join(f"{t.term_en} = {t.approved_ar}" + (f" (female: {t.female_ar})" if t.female_ar else "")
                       for t in terms_in(r["source_text"], terms))
        female = db.get_string(conn, r["string_key"], "female") if r["gender"] == "male" else None
        lines.append([batch_id, r["string_key"], r["category"] or "",
                      "male+female" if r["gender"] == "male" else "shared",
                      r["speaker"] or "", r["context"] or "",
                      "" if r["max_length"] is None else r["max_length"],
                      "" if r["max_line_length"] is None else r["max_line_length"],
                      r["source_text"], " | ".join(find_tokens(r["source_text"])), gl, h, "", "", "",
                      r["translation"] or "", (female["translation"] or "") if female else "",
                      " | ".join(_version_issues(conn, r["string_key"], terms))])
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


def import_batch(conn, csv_path, translator: str = MACHINE_MODEL, overwrite_drafts: bool = False,
                 report_dir: Path | None = None, dry_run: bool = False) -> dict:
    """Import a returned batch file. Never approves anything and never overwrites an approved
    version (to replace one, a human runs 'reject' first). dry_run=True: full checks and report,
    but nothing stays in the database."""
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
    else:
        results_now = [r["result"] for r in conn.execute("SELECT result FROM batch_items WHERE batch_id=?",
                                                          (batch_id,))]
        if results_now and all(x == CANCELLED for x in results_now):
            problems.append(f"batch {batch_id} was cancelled; export a new batch")
    seen = {}
    for line, r in enumerate(rows, start=2):
        rid = (r.get("id") or "").strip()
        if rid in seen:
            problems.append(f"line {line}: duplicate id {rid!r} (first on line {seen[rid]})")
        seen.setdefault(rid, line)
        for col in ("translation", "translation_female"):
            tr = r.get(col) or ""
            if tr.strip() and _looks_like_lost_encoding(tr):
                problems.append(f"line {line}: {col} of {rid!r} looks like lost Arabic encoding: {tr[:30]!r}")
    if problems:
        raise BatchError(f"{csv_path}: file refused, nothing imported", problems)

    report = []
    stats = {"accepted_review": 0, "accepted_draft": 0, "unchanged": 0, "rejected": 0, "skipped": 0,
             "errors": 0, "warnings": 0, "split": 0, "dry_run": dry_run}
    ignored = sorted(APPROVAL_COLUMNS & set(rows[0]))
    stats["ignored_columns"] = ignored
    if ignored:
        report.append(("", "", "", "", "warning", "ignored_columns",
                       "approval-related columns are never imported and were ignored: " + ", ".join(ignored),
                       "", ""))
        stats["warnings"] += 1

    with _dry_run_scope(conn, dry_run):
        _import_rows(conn, rows, batch_id, translator, overwrite_drafts, stats, report)
        stats["approved_by_import"] = stats.pop("_approved_by_import")

    stats["batch_id"] = batch_id
    sev_order = {"error": 0, "warning": 1, "info": 2, "": 3}
    report.sort(key=lambda x: (sev_order[x[4]], x[0], x[1]))
    name = f"{batch_id}_report{'_dryrun' if dry_run else ''}.csv"
    stats["report"] = _write_report(Path(report_dir or LOGS_DIR) / name,
                                    ["id", "gender", "result", "status_after", "severity", "check", "message",
                                     "source", "translation"], report)
    return stats


def _import_rows(conn, rows, batch_id, translator, overwrite_drafts, stats, report):
    items = {r["string_key"]: r for r in conn.execute("SELECT * FROM batch_items WHERE batch_id=?", (batch_id,))}
    terms = _terms(conn)
    results = {}
    has_female_col = "translation_female" in rows[0]
    history_start = conn.execute("SELECT coalesce(max(id), 0) FROM history").fetchone()[0]

    def add(rid, gender, outcome, status, issues, source="", text=""):
        if not issues:
            report.append((rid, gender, outcome, status, "", "", "", source, text))
        for i in issues:
            if i.severity == "error":
                stats["errors"] += 1
            elif i.severity == "warning":
                stats["warnings"] += 1
            report.append((rid, gender, outcome, status, i.severity, i.check, i.message, source, text))

    def reject(rid, reason, msg, item=None, gender=""):
        stats["rejected"] += 1
        report.append((rid, gender, "rejected", "", "error", reason, msg, "", ""))
        if item is not None:
            results[item["string_id"]] = "rejected"

    def protection(row) -> tuple[str, str] | None:
        if row["status"] == "approved":
            return ("approved_protected", "version is approved; imports never replace an approved translation. "
                                          "A reviewer must 'reject' it first if it should change")
        if row["status"] in ("draft", "needs_review") and row["origin"] == "human" \
                and row["translation"] and not overwrite_drafts:
            return ("human_draft_protected", f"version has a human translation ({row['status']}); "
                                             f"use --overwrite-drafts to replace it")
        return None

    def finish(row_id, extra_issues=()):
        """Validate one version; errors keep it in draft, otherwise it goes to needs_review."""
        row = conn.execute("SELECT * FROM strings WHERE id=?", (row_id,)).fetchone()
        issues = check_string(row["source_text"], row["translation"], "draft", row["max_length"],
                              row["max_line_length"], terms, gender=row["gender"], category=row["category"])
        if any(i.severity == "error" for i in issues):
            outcome = "accepted_draft"
        else:
            if row["status"] == "draft":
                db._log(conn, row, "needs_review", row["translation"], row["origin"], "batch-import")
                conn.execute("UPDATE strings SET status='needs_review', updated_at=datetime('now') WHERE id=?",
                             (row_id,))
            outcome = "accepted_review"
        conn.execute("DELETE FROM validation_issues WHERE string_id=?", (row_id,))
        for i in issues:
            conn.execute("INSERT INTO validation_issues(string_id, severity, check_name, message) VALUES (?,?,?,?)",
                         (row_id, i.severity, i.check, i.message))
        row = conn.execute("SELECT * FROM strings WHERE id=?", (row_id,)).fetchone()
        return outcome, row, list(extra_issues) + issues

    def apply_version(row, text, note, src):
        """Write one version (shared, male or female). Returns its outcome."""
        if text == row["translation"]:
            add(row["string_key"], row["gender"], "unchanged", row["status"],
                [Issue("info", "unchanged", "translation identical to the database")], src, text)
            return "unchanged"
        prot = protection(row)
        if prot:
            report.append((row["string_key"], row["gender"], "rejected", row["status"], "error", prot[0], prot[1],
                           "", ""))
            return "rejected"
        overwrite_note = None
        if row["translation"]:
            overwrite_note = f"replaced previous {row['status']} ({row['origin']}) translation"
        db._set_row_translation(conn, row, text, "machine", translator, MACHINE_MODEL,
                                "; ".join(x for x in (f"batch {batch_id}", overwrite_note, note) if x))
        extra = [Issue("warning", "overwrite", overwrite_note)] if overwrite_note else []
        if note:
            extra.append(Issue("info", "translator_note", note))
        outcome, after, issues = finish(row["id"], extra)
        add(row["string_key"], row["gender"], outcome, after["status"], issues, src, text)
        return outcome

    order = ["skipped", "unchanged", "accepted_review", "accepted_draft", "rejected"]

    for r in rows:
        rid = r["id"].strip()
        tr = r.get("translation") or ""
        trf = (r.get("translation_female") or "") if has_female_col else ""
        note = (r.get("translator_note") or "").strip() or None
        src = r.get("source") or ""
        if not conn.execute("SELECT 1 FROM strings WHERE string_key=?", (rid,)).fetchone():
            reject(rid, "unknown_id", "id does not exist in the database")
            continue
        current = db.get_string(conn, rid)                       # shared, or male of a split string
        item = items.get(rid)
        if item is None:
            reject(rid, "not_in_batch", f"id was not exported in {batch_id}")
            continue
        if item["result"] == CANCELLED:
            reject(rid, "batch_cancelled", f"this row was cancelled in {batch_id}; export a new batch")
            continue                                             # result stays 'cancelled'
        # fingerprints: file source vs export, DB source vs export (same source for both versions)
        if source_hash(src) != item["source_hash"] or (r.get("source_hash") or "").strip() != item["source_hash"]:
            reject(rid, "modified_source", "the 'source' or 'source_hash' column was changed after export", item)
            continue
        if source_hash(current["source_text"]) != item["source_hash"]:
            reject(rid, "stale", "English source changed in the DB since export; export a new batch", item)
            continue
        if not tr.strip():
            if trf.strip():
                reject(rid, "female_without_male",
                       "translation_female is filled but translation (male/shared) is empty", item)
                continue
            stats["skipped"] += 1
            results[item["string_id"]] = "skipped"
            report.append((rid, current["gender"], "skipped", current["status"], "info", "empty",
                           "no translation in file", "", ""))
            continue

        if current["gender"] == "shared":
            if trf.strip() and trf.strip() != tr.strip():
                # ---- split into male + female
                prot = protection(current)
                if prot:
                    reject(rid, prot[0], prot[1] + " (needed to split into male/female)", item, "shared")
                    continue
                db.split_gender(conn, rid, trf, translator, male_text=tr, origin="machine",
                                machine_model=MACHINE_MODEL,
                                note="; ".join(x for x in (f"batch {batch_id}", note) if x))
                stats["split"] += 1
                outs = []
                for g, text in (("male", tr), ("female", trf)):
                    row = db.get_string(conn, rid, g)
                    extra = [Issue("info", "split", "string split into male/female versions")]
                    if note:
                        extra.append(Issue("info", "translator_note", note))
                    outcome, after, issues = finish(row["id"], extra)
                    add(rid, g, outcome, after["status"], issues, src, text)
                    outs.append(outcome)
                outcome = max(outs, key=order.index)
            else:
                if trf.strip():
                    report.append((rid, "shared", "", current["status"], "warning", "female_identical",
                                   "translation_female equals translation; kept one shared translation", src, trf))
                    stats["warnings"] += 1
                outcome = apply_version(current, tr, note, src)
        else:
            # ---- already split: update the versions given in the file
            outs = [apply_version(current, tr, note, src)]
            if trf.strip():
                outs.append(apply_version(db.get_string(conn, rid, "female"), trf, None, src))
            elif tr != current["translation"]:
                report.append((rid, "female", "", db.get_string(conn, rid, "female")["status"], "warning",
                               "female_unchanged", "file has no translation_female; the female version was "
                               "not updated", "", ""))
                stats["warnings"] += 1
            outcome = max(outs, key=order.index)

        if outcome == "rejected":
            stats["rejected"] += 1
        else:
            stats[outcome] += 1
        results[item["string_id"]] = outcome
        # checks between the two versions (identical / diacritics only / different codes)
        if db.is_gendered(conn, rid):
            for vid, issues in pair_issues(db.versions(conn, rid)).items():
                v = conn.execute("SELECT gender, status FROM strings WHERE id=?", (vid,)).fetchone()
                for i in issues:
                    if i.severity == "warning":
                        stats["warnings"] += 1
                    report.append((rid, v["gender"], outcome, v["status"], i.severity, i.check, i.message, "", ""))

    # consistency warnings against the whole DB (same English, different Arabic)
    all_rows = conn.execute("SELECT * FROM strings").fetchall()
    batch_string_ids = {i["string_id"] for i in items.values()}
    for sid, issues in duplicate_issues(all_rows).items():
        if sid in batch_string_ids and results.get(sid) in ("accepted_review", "accepted_draft"):
            key = conn.execute("SELECT string_key, gender, status FROM strings WHERE id=?", (sid,)).fetchone()
            for i in issues:
                if i.severity == "warning":
                    stats["warnings"] += 1
                report.append((key["string_key"], key["gender"], results[sid], key["status"], i.severity, i.check,
                               i.message, "", ""))

    for sid, res in results.items():
        conn.execute("UPDATE batch_items SET result=? WHERE batch_id=? AND string_id=? AND "
                     "(result IS NULL OR result <> ?)", (res, batch_id, sid, CANCELLED))
    open_left = conn.execute("SELECT COUNT(*) FROM batch_items WHERE batch_id=? AND (result IS NULL OR result IN"
                             " ('rejected','skipped','accepted_draft'))", (batch_id,)).fetchone()[0]
    conn.execute("UPDATE batches SET status=?, imported_at=datetime('now') WHERE batch_id=?",
                 ("imported" if open_left == 0 else "partially_imported", batch_id))
    stats["_approved_by_import"] = conn.execute(          # must always be 0
        "SELECT COUNT(*) FROM history WHERE id > ? AND new_status='approved'", (history_start,)).fetchone()[0]


# ------------------------------------------------------------------ 4. cancel
def cancel_batch(conn, batch_id: str, by: str, reason: str | None = None) -> dict:
    """Take the rows of an exported batch that never came back out of it, so they can be exported
    again. Nothing is deleted: the rows' batch_items.result becomes 'cancelled' and the batch note
    records who cancelled it and why. Translations and statuses are not touched."""
    if not by or not by.strip():
        raise BatchError("--by is required: who cancels the batch")
    b = conn.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
    if b is None:
        raise BatchError(f"unknown batch_id {batch_id!r}")
    open_rows = conn.execute("SELECT COUNT(*) FROM batch_items WHERE batch_id=? AND result IS NULL",
                             (batch_id,)).fetchone()[0]
    if open_rows == 0:
        raise BatchError(f"batch {batch_id} has no open rows (already imported or cancelled); nothing to cancel")
    conn.execute("UPDATE batch_items SET result=? WHERE batch_id=? AND result IS NULL", (CANCELLED, batch_id))
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"cancelled {stamp} by {by.strip()}: {open_rows} open row(s)" + (f"; reason: {reason}" if reason else "")
    conn.execute("UPDATE batches SET note = CASE WHEN note IS NULL OR note = '' THEN ? ELSE note || char(10) || ? END"
                 " WHERE batch_id=?", (line, line, batch_id))
    return {"batch_id": batch_id, "cancelled_rows": open_rows,
            "kept_rows": b["row_count"] - open_rows, "note": line}


def list_batches(conn) -> list[dict]:
    out = []
    for b in conn.execute("SELECT * FROM batches ORDER BY created_at, batch_id"):
        counts = {r["result"] or "waiting": r["n"] for r in conn.execute(
            "SELECT result, COUNT(*) n FROM batch_items WHERE batch_id=? GROUP BY result", (b["batch_id"],))}
        out.append({"batch_id": b["batch_id"], "status": b["status"], "rows": b["row_count"],
                    "created_at": b["created_at"], "imported_at": b["imported_at"], "results": counts,
                    "note": b["note"]})
    return out
