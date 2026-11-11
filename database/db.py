"""Database access and the translation status workflow.

Statuses
  pending       no translation yet
  draft         a translation exists but nobody reviewed it (all machine output lands here)
  needs_review  a translator marked it ready; waiting for a human reviewer
  approved      a named human reviewer accepted it; only these go into the mod later

Hard rules (enforced here AND by CHECK constraints in schema.sql)
  * machine output can only ever create a 'draft'
  * 'approved' needs a named human reviewer and the row must be in 'needs_review'
  * changing the text of a reviewed/approved row sends it back to 'draft'
  * a changed English source sends an approved row back to 'needs_review'
  * CSV import never creates 'approved' rows
"""
import csv
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from common.paths import DEFAULT_DB, SCHEMA_FILE, ensure_dir

STATUSES = ("pending", "draft", "needs_review", "approved")
ORIGINS = ("none", "machine", "human")
MACHINE_NAMES = {"ollama", "machine", "ai", "llm", "bot", "auto", "claude"}

CSV_COLUMNS = [
    "string_key", "category", "speaker", "context", "source_text",
    "max_length", "max_line_length", "translation", "status", "origin",
    "machine_model", "translator", "reviewer", "notes",
    "source_file", "fmg_name", "entry_id",
]


class WorkflowError(Exception):
    """Raised when a status change breaks the workflow rules."""


# ---------------------------------------------------------------- connection
def connect(db_path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    db_path = Path(db_path)
    ensure_dir(db_path.parent)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', '1')")
    conn.commit()


@contextmanager
def open_db(db_path: Path | str = DEFAULT_DB):
    conn = connect(db_path)
    try:
        init_db(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------- helpers
def _clean(value):
    if value is None:
        return None
    value = str(value)
    return value if value.strip() != "" else None


def _int_or_none(value):
    value = _clean(value)
    return int(value) if value is not None else None


def get_string(conn, string_key: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM strings WHERE string_key = ?", (string_key,)).fetchone()
    if row is None:
        raise KeyError(f"unknown string_key: {string_key}")
    return row


def _log(conn, row, new_status, new_translation, origin, by, note=None):
    conn.execute(
        "INSERT INTO history(string_id, old_status, new_status, old_translation, new_translation,"
        " origin, changed_by, note) VALUES (?,?,?,?,?,?,?,?)",
        (row["id"], row["status"], new_status, row["translation"], new_translation, origin, by, note),
    )


def _is_machine_name(name) -> bool:
    return name is None or name.strip() == "" or name.strip().lower() in MACHINE_NAMES


# ---------------------------------------------------------------- strings
def upsert_source(conn, string_key: str, source_text: str, **fields) -> str:
    """Insert a new source string or update its source-side fields.

    Returns 'inserted', 'updated', 'source_changed' or 'unchanged'.
    If the English source changed on a translated row, the row goes back to review.
    """
    allowed = {"category", "speaker", "context", "max_length", "max_line_length",
               "source_file", "fmg_name", "entry_id"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    for k in ("max_length", "max_line_length", "entry_id"):
        if k in fields:
            fields[k] = _int_or_none(fields[k])
    for k in ("category", "speaker", "context", "source_file", "fmg_name"):
        if k in fields:
            fields[k] = _clean(fields[k])

    existing = conn.execute("SELECT * FROM strings WHERE string_key = ?", (string_key,)).fetchone()
    if existing is None:
        cols = ["string_key", "source_text"] + list(fields)
        conn.execute(
            f"INSERT INTO strings({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [string_key, source_text] + list(fields.values()),
        )
        return "inserted"

    changes = {k: v for k, v in fields.items() if existing[k] != v}
    source_changed = existing["source_text"] != source_text
    if not changes and not source_changed:
        return "unchanged"

    sets = dict(changes)
    if source_changed:
        sets["source_text"] = source_text
        if existing["status"] == "approved":
            _log(conn, existing, "needs_review", existing["translation"], existing["origin"],
                 "system", "English source changed")
            sets["status"] = "needs_review"
            sets["reviewer"] = None
    assignments = ", ".join(f"{k} = ?" for k in sets) + ", updated_at = datetime('now')"
    conn.execute(f"UPDATE strings SET {assignments} WHERE id = ?", list(sets.values()) + [existing["id"]])
    return "source_changed" if source_changed else "updated"


def set_translation(conn, string_key: str, text, origin: str, by: str,
                    machine_model: str | None = None, note: str | None = None) -> str:
    """Store a translation. Always results in 'pending' (empty text) or 'draft'.

    origin='machine' : used by the Ollama step later. Never anything but draft.
    origin='human'   : a person typed it.
    """
    if origin not in ("machine", "human"):
        raise WorkflowError(f"origin must be 'machine' or 'human', got {origin!r}")
    row = get_string(conn, string_key)
    text = _clean(text)
    if text is None:
        new_status, new_origin = "pending", "none"
    else:
        new_status, new_origin = "draft", origin
    if text == row["translation"] and new_status == row["status"]:
        return row["status"]
    _log(conn, row, new_status, text, new_origin, by, note)
    conn.execute(
        "UPDATE strings SET translation = ?, status = ?, origin = ?, machine_model = ?,"
        " translator = ?, reviewer = NULL, updated_at = datetime('now') WHERE id = ?",
        (text, new_status, new_origin, machine_model if origin == "machine" else None,
         by if origin == "human" else row["translator"], row["id"]),
    )
    return new_status


def mark_for_review(conn, string_key: str, by: str) -> None:
    row = get_string(conn, string_key)
    if row["status"] != "draft":
        raise WorkflowError(f"{string_key}: only 'draft' can go to review (is {row['status']})")
    _log(conn, row, "needs_review", row["translation"], row["origin"], by)
    conn.execute("UPDATE strings SET status='needs_review', updated_at=datetime('now') WHERE id=?",
                 (row["id"],))


def approve(conn, string_key: str, reviewer: str, blocking_errors: list | None = None) -> None:
    """Approve a reviewed string. `blocking_errors` = validator errors for this row."""
    row = get_string(conn, string_key)
    if _is_machine_name(reviewer):
        raise WorkflowError("approval needs the name of a human reviewer")
    if row["status"] != "needs_review":
        raise WorkflowError(f"{string_key}: only 'needs_review' can be approved (is {row['status']})")
    if blocking_errors:
        raise WorkflowError(f"{string_key}: fix validation errors first: " + "; ".join(blocking_errors))
    _log(conn, row, "approved", row["translation"], row["origin"], reviewer)
    conn.execute("UPDATE strings SET status='approved', reviewer=?, updated_at=datetime('now') WHERE id=?",
                 (reviewer.strip(), row["id"]))


def reject(conn, string_key: str, by: str, note: str | None = None) -> None:
    """Send a row from needs_review/approved back to draft."""
    row = get_string(conn, string_key)
    if row["status"] not in ("needs_review", "approved"):
        raise WorkflowError(f"{string_key}: nothing to reject (is {row['status']})")
    _log(conn, row, "draft", row["translation"], row["origin"], by, note)
    conn.execute("UPDATE strings SET status='draft', reviewer=NULL, updated_at=datetime('now') WHERE id=?",
                 (row["id"],))


def status_counts(conn) -> dict:
    counts = {s: 0 for s in STATUSES}
    for r in conn.execute("SELECT status, COUNT(*) n FROM strings GROUP BY status"):
        counts[r["status"]] = r["n"]
    machine = conn.execute(
        "SELECT COUNT(*) FROM strings WHERE origin='machine' AND status<>'approved'").fetchone()[0]
    counts["machine_unreviewed"] = machine
    return counts


# ---------------------------------------------------------------- CSV
def import_csv(conn, csv_path: Path | str, by: str = "csv-import") -> dict:
    """Import strings (and optional translations) from a UTF-8 CSV.

    * new keys are inserted, existing keys get their source fields updated
    * a translation in the CSV that differs from the DB becomes a 'draft'
      (origin from the 'origin' column, default human)
    * 'needs_review' in the CSV is honoured; 'approved' is downgraded to 'needs_review'
    """
    stats = {"inserted": 0, "updated": 0, "source_changed": 0, "unchanged": 0,
             "translations_set": 0, "approved_downgraded": 0, "skipped": 0}
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if "string_key" not in (reader.fieldnames or []) or "source_text" not in reader.fieldnames:
            raise ValueError("CSV needs at least the columns string_key and source_text")
        for rec in reader:
            key = _clean(rec.get("string_key"))
            source = rec.get("source_text")
            if key is None or source is None:
                stats["skipped"] += 1
                continue
            extra = {k: v for k, v in rec.items() if k not in ("string_key", "source_text")}
            result = upsert_source(conn, key, source, **extra)
            stats[result] += 1

            translation = _clean(rec.get("translation"))
            row = get_string(conn, key)
            if translation is not None and translation != row["translation"]:
                origin = (_clean(rec.get("origin")) or "human").lower()
                origin = origin if origin in ("machine", "human") else "human"
                who = _clean(rec.get("translator")) or by
                set_translation(conn, key, translation, origin, who,
                                machine_model=_clean(rec.get("machine_model")))
                stats["translations_set"] += 1
                wanted = (_clean(rec.get("status")) or "draft").lower()
                if wanted == "approved":
                    stats["approved_downgraded"] += 1
                    wanted = "needs_review"
                if wanted == "needs_review":
                    mark_for_review(conn, key, who)
    return stats


def export_csv(conn, csv_path: Path | str, status: str | None = None) -> int:
    """Export strings to CSV (UTF-8 with BOM so Excel on Windows shows Arabic correctly)."""
    sql = f"SELECT {','.join(CSV_COLUMNS)} FROM strings"
    params = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY string_key"
    rows = conn.execute(sql, params).fetchall()
    csv_path = Path(csv_path)
    ensure_dir(csv_path.parent)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        for r in rows:
            w.writerow(["" if r[c] is None else r[c] for c in CSV_COLUMNS])
    return len(rows)
