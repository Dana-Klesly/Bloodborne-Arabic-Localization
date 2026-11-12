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
# names that may never act as a human reviewer (includes legacy machine-translator names)
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


SCHEMA_VERSION = 2


class MigrationRequired(Exception):
    """The database uses an older schema; run `cli.py migrate` (it makes a backup first)."""


def _schema_statements() -> list[str]:
    """schema.sql split into single statements (trigger bodies kept whole), PRAGMAs skipped."""
    statements, buf = [], ""
    for line in SCHEMA_FILE.read_text(encoding="utf-8").splitlines(keepends=True):
        if not buf and (not line.strip() or line.lstrip().startswith("--")):
            continue
        buf += line
        if sqlite3.complete_statement(buf):
            if not buf.lstrip().upper().startswith("PRAGMA"):
                statements.append(buf.strip())
            buf = ""
    if buf.strip():
        raise ValueError("schema.sql ends with an incomplete statement")
    return statements


def schema_version(conn: sqlite3.Connection) -> int:
    """0 = empty database, otherwise meta.schema_version."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='strings'").fetchone():
        return 0
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row else 1


def init_db(conn: sqlite3.Connection) -> None:
    """Create a new database, or open an up-to-date one. Never migrates silently."""
    version = schema_version(conn)
    if 0 < version < SCHEMA_VERSION:
        raise MigrationRequired(
            f"database schema is v{version}, this version of the tools needs v{SCHEMA_VERSION}. "
            f"Run:  .venv\\Scripts\\python.exe cli.py migrate   (a verified backup is made first)")
    if version > SCHEMA_VERSION:
        raise MigrationRequired(f"database schema v{version} is newer than these tools (v{SCHEMA_VERSION})")
    for stmt in _schema_statements():
        conn.execute(stmt)
    if version == 0:
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    conn.commit()


# ---------------------------------------------------------------- migration v1 -> v2
_TABLES = ("meta", "strings", "history", "glossary", "batches", "batch_items", "validation_issues")
_V1_STRING_COLUMNS = ["id", "string_key", "source_file", "fmg_name", "entry_id", "category", "speaker",
                      "context", "source_text", "max_length", "max_line_length", "translation", "status",
                      "origin", "machine_model", "translator", "reviewer", "notes", "created_at", "updated_at"]


def _snapshot(conn) -> dict:
    """Everything that must survive the migration unchanged."""
    snap = {"counts": {}, "rows": {}}
    for t in _TABLES:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone():
            snap["counts"][t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            snap["rows"][t] = sorted(tuple(r) for r in conn.execute(f"SELECT * FROM {t}"))
    snap["strings_v1"] = sorted(tuple(r) for r in conn.execute(
        f"SELECT {','.join(_V1_STRING_COLUMNS)} FROM strings"))
    return snap


def _integrity(conn) -> tuple[str, list]:
    return (conn.execute("PRAGMA integrity_check").fetchone()[0],
            [tuple(r) for r in conn.execute("PRAGMA foreign_key_check")])


def backup_database(conn, backup_dir: Path, label: str) -> dict:
    """Consistent copy with SQLite's backup API, then verified (integrity + identical content)."""
    import datetime as _dt
    ensure_dir(Path(backup_dir))
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path(backup_dir) / f"localization_{label}_{stamp}.db"
    n = 1
    while path.exists():
        n += 1
        path = Path(backup_dir) / f"localization_{label}_{stamp}_{n}.db"
    dest = sqlite3.connect(path)
    try:
        conn.backup(dest)
    finally:
        dest.close()
    check = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        integrity, fk = _integrity(check)
        same = _snapshot(check)["rows"] == _snapshot(conn)["rows"]
    finally:
        check.close()
    if integrity != "ok" or fk or not same:
        raise RuntimeError(f"backup verification failed ({path}): integrity={integrity}, fk={fk}, identical={same}")
    return {"path": str(path), "bytes": path.stat().st_size, "integrity": integrity, "identical": same}


def _verify_v2(conn, before: dict) -> None:
    """Raise if anything was lost or changed by the migration."""
    after = _snapshot(conn)
    problems = []
    for t, n in before["counts"].items():
        if after["counts"].get(t) != n:
            problems.append(f"{t}: {n} rows before, {after['counts'].get(t)} after")
    if after["strings_v1"] != before["strings_v1"]:
        problems.append("strings: ids or values changed")
    for t in ("history", "glossary", "batches", "batch_items", "validation_issues"):
        if t in before["rows"]:
            cols_before = len(before["rows"][t][0]) if before["rows"][t] else 0
            trimmed = sorted(r[:cols_before] for r in after["rows"][t]) if cols_before else after["rows"][t]
            if trimmed != before["rows"][t]:
                problems.append(f"{t}: content changed")
    if conn.execute("SELECT COUNT(*) FROM strings WHERE gender <> 'shared'").fetchone()[0]:
        problems.append("strings: rows not marked 'shared'")
    integrity, fk = _integrity(conn)
    if integrity != "ok":
        problems.append(f"integrity_check: {integrity}")
    if fk:
        problems.append(f"foreign_key_check: {fk}")
    leftovers = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE name LIKE '%v1_old%'")]
    if leftovers:
        problems.append(f"leftover objects: {leftovers}")
    if problems:
        raise RuntimeError("migration verification failed: " + "; ".join(problems))


def migrate(conn: sqlite3.Connection, backup_dir: Path) -> dict:
    """Upgrade a v1 database to v2 in one transaction, after a verified backup.

    * strings is rebuilt with a 'gender' column; every row keeps its internal id and
      becomes 'shared'; history / batch_items / validation_issues stay linked by id
    * glossary gets female_ar / female_variants_ar (empty)
    * meta: only schema_version changes (e.g. an active gender choice is untouched)
    * on any problem everything is rolled back; the backup stays in place
    """
    version = schema_version(conn)
    report = {"version_before": version}
    if version == SCHEMA_VERSION:
        report.update(version_after=version, migrated=False, message="already up to date")
        return report
    if version != 1:
        raise MigrationRequired(f"cannot migrate from schema v{version}")

    integrity, fk = _integrity(conn)
    if integrity != "ok" or fk:
        raise RuntimeError(f"database is not healthy, migration stopped: integrity={integrity}, fk={fk}")
    report["backup"] = backup_database(conn, backup_dir, "v1")
    before = _snapshot(conn)
    meta_before = dict(conn.execute("SELECT key, value FROM meta").fetchall())

    old_isolation = conn.isolation_level
    conn.commit()
    conn.isolation_level = None                     # manual transaction control
    conn.execute("PRAGMA foreign_keys = OFF")       # must be outside the transaction
    conn.execute("PRAGMA legacy_alter_table = ON")  # keep other tables' REFERENCES strings(id)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("ALTER TABLE strings RENAME TO strings_v1_old")
        for idx in ("idx_strings_status", "idx_strings_category", "idx_strings_source"):
            conn.execute(f"DROP INDEX IF EXISTS {idx}")
        for stmt in _schema_statements():           # creates the new strings + indexes + triggers
            conn.execute(stmt)
        cols = ",".join(_V1_STRING_COLUMNS)
        conn.execute(f"INSERT INTO strings({cols}, gender) SELECT {cols}, 'shared' FROM strings_v1_old")
        conn.execute("DROP TABLE strings_v1_old")
        existing = {r[1] for r in conn.execute("PRAGMA table_info(glossary)")}
        for col in ("female_ar", "female_variants_ar"):
            if col not in existing:
                conn.execute(f"ALTER TABLE glossary ADD COLUMN {col} TEXT")
        _verify_v2(conn, before)
        conn.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
        meta_after = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        if {k: v for k, v in meta_after.items() if k != "schema_version"} != \
                {k: v for k, v in meta_before.items() if k != "schema_version"}:
            raise RuntimeError("migration verification failed: meta changed")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA legacy_alter_table = OFF")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.isolation_level = old_isolation

    integrity, fk = _integrity(conn)
    report.update(version_after=schema_version(conn), migrated=True, integrity=integrity,
                  foreign_key_problems=fk, counts=_snapshot(conn)["counts"])
    return report


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


GENDERS = ("male", "female")
ALL_GENDERS = ("shared", "male", "female")


def get_string(conn, string_key: str, gender: str | None = None) -> sqlite3.Row:
    """Return one version of a string.

    gender=None  -> the base row (the 'shared' row, or the 'male' row of a split string);
                    this keeps every existing caller working unchanged
    gender='male' / 'female' -> that version (KeyError if it does not exist)
    """
    if gender is None:
        row = conn.execute("SELECT * FROM strings WHERE string_key = ? AND gender IN ('shared','male')",
                           (string_key,)).fetchone()
    else:
        if gender not in ALL_GENDERS:
            raise WorkflowError(f"gender must be one of {', '.join(ALL_GENDERS)}, got {gender!r}")
        row = conn.execute("SELECT * FROM strings WHERE string_key = ? AND gender = ?",
                           (string_key, gender)).fetchone()
    if row is None:
        raise KeyError(f"unknown string_key: {string_key}" + (f" ({gender})" if gender else ""))
    return row


def is_gendered(conn, string_key: str) -> bool:
    return conn.execute("SELECT 1 FROM strings WHERE string_key=? AND gender='male'", (string_key,)).fetchone() is not None


def versions(conn, string_key: str) -> list[sqlite3.Row]:
    rows = conn.execute("SELECT * FROM strings WHERE string_key=? ORDER BY CASE gender"
                        " WHEN 'shared' THEN 0 WHEN 'male' THEN 1 ELSE 2 END", (string_key,)).fetchall()
    if not rows:
        raise KeyError(f"unknown string_key: {string_key}")
    return rows


def resolve(conn, string_key: str, gender: str | None) -> sqlite3.Row:
    """Version addressed by a user command. Refuses to guess for split strings."""
    if gender is None:
        if is_gendered(conn, string_key):
            raise WorkflowError(f"{string_key} has male and female versions; add --gender male or --gender female")
        return get_string(conn, string_key)
    if gender not in GENDERS:
        raise WorkflowError(f"--gender must be male or female, got {gender!r}")
    if not is_gendered(conn, string_key):
        get_string(conn, string_key)            # KeyError if unknown
        raise WorkflowError(f"{string_key} is shared (one translation for both); do not pass --gender")
    return get_string(conn, string_key, gender)


def _log(conn, row, new_status, new_translation, origin, by, note=None):
    conn.execute(
        "INSERT INTO history(string_id, old_status, new_status, old_translation, new_translation,"
        " origin, changed_by, note) VALUES (?,?,?,?,?,?,?,?)",
        (row["id"], row["status"], new_status, row["translation"], new_translation, origin, by, note),
    )


def _is_machine_name(name) -> bool:
    return name is None or name.strip() == "" or name.strip().lower() in MACHINE_NAMES


# ---------------------------------------------------------------- strings
_SOURCE_FIELDS = ("category", "speaker", "context", "max_length", "max_line_length",
                  "source_file", "fmg_name", "entry_id")


def upsert_source(conn, string_key: str, source_text: str, **fields) -> str:
    """Insert a new source string or update its source-side fields (on every version).

    Returns 'inserted', 'updated', 'source_changed' or 'unchanged'.
    If the English source changed, every approved version goes back to 'needs_review'
    (drafts stay drafts: they still have to pass the validator before review).
    """
    fields = {k: v for k, v in fields.items() if k in _SOURCE_FIELDS}
    for k in ("max_length", "max_line_length", "entry_id"):
        if k in fields:
            fields[k] = _int_or_none(fields[k])
    for k in ("category", "speaker", "context", "source_file", "fmg_name"):
        if k in fields:
            fields[k] = _clean(fields[k])

    rows = conn.execute("SELECT * FROM strings WHERE string_key = ?", (string_key,)).fetchall()
    if not rows:
        cols = ["string_key", "source_text"] + list(fields)
        conn.execute(
            f"INSERT INTO strings({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [string_key, source_text] + list(fields.values()),
        )
        return "inserted"

    base = next(r for r in rows if r["gender"] in ("shared", "male"))
    changes = {k: v for k, v in fields.items() if base[k] != v}
    source_changed = base["source_text"] != source_text
    if not changes and not source_changed:
        return "unchanged"

    for existing in rows:
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


def _set_row_translation(conn, row, text, origin, by, machine_model=None, note=None) -> str:
    if origin not in ("machine", "human"):
        raise WorkflowError(f"origin must be 'machine' or 'human', got {origin!r}")
    text = _clean(text)
    if text is None and row["gender"] != "shared":
        raise WorkflowError(f"{row['string_key']} ({row['gender']}): a male/female version cannot be emptied; "
                            f"use gender-merge to go back to one shared translation")
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


def set_translation(conn, string_key: str, text, origin: str, by: str,
                    machine_model: str | None = None, note: str | None = None,
                    gender: str | None = None) -> str:
    """Store a translation. Always results in 'pending' (empty text, shared only) or 'draft'.

    origin='machine' : automatic translation (e.g. an AI batch). Never anything but draft here.
    origin='human'   : a person typed it.
    """
    return _set_row_translation(conn, resolve(conn, string_key, gender), text, origin, by, machine_model, note)


def mark_for_review(conn, string_key: str, by: str, gender: str | None = None) -> None:
    row = resolve(conn, string_key, gender)
    if row["status"] != "draft":
        raise WorkflowError(f"{_label(row)}: only 'draft' can go to review (is {row['status']})")
    _log(conn, row, "needs_review", row["translation"], row["origin"], by)
    conn.execute("UPDATE strings SET status='needs_review', updated_at=datetime('now') WHERE id=?",
                 (row["id"],))


def approve(conn, string_key: str, reviewer: str, blocking_errors: list | None = None,
            gender: str | None = None) -> None:
    """Approve ONE version. `blocking_errors` = validator errors for this version."""
    row = resolve(conn, string_key, gender)
    if _is_machine_name(reviewer):
        raise WorkflowError("approval needs the name of a human reviewer")
    if row["status"] != "needs_review":
        raise WorkflowError(f"{_label(row)}: only 'needs_review' can be approved (is {row['status']})")
    if blocking_errors:
        raise WorkflowError(f"{_label(row)}: fix validation errors first: " + "; ".join(blocking_errors))
    _log(conn, row, "approved", row["translation"], row["origin"], reviewer)
    conn.execute("UPDATE strings SET status='approved', reviewer=?, updated_at=datetime('now') WHERE id=?",
                 (reviewer.strip(), row["id"]))


def reject(conn, string_key: str, by: str, note: str | None = None, gender: str | None = None) -> None:
    """Send one version from needs_review/approved back to draft."""
    row = resolve(conn, string_key, gender)
    if row["status"] not in ("needs_review", "approved"):
        raise WorkflowError(f"{_label(row)}: nothing to reject (is {row['status']})")
    _log(conn, row, "draft", row["translation"], row["origin"], by, note)
    conn.execute("UPDATE strings SET status='draft', reviewer=NULL, updated_at=datetime('now') WHERE id=?",
                 (row["id"],))


def _label(row) -> str:
    return row["string_key"] + ("" if row["gender"] == "shared" else f" ({row['gender']})")


# ---------------------------------------------------------------- male / female versions
_COPY_ON_SPLIT = ("string_key", "source_file", "fmg_name", "entry_id", "category", "speaker", "context",
                  "source_text", "max_length", "max_line_length")


def split_gender(conn, string_key: str, female_text: str, by: str, male_text: str | None = None,
                 origin: str = "human", machine_model: str | None = None, note: str | None = None) -> dict:
    """Turn a shared string into a male + female pair (same string_key).

    * the shared row becomes 'male' and keeps its internal id and history
    * male text = male_text, or the existing shared translation
    * an approved shared row goes back to 'needs_review' (its meaning changed: male only)
    * the new female row starts as 'draft'
    """
    base = get_string(conn, string_key)
    if base["gender"] != "shared":
        raise WorkflowError(f"{string_key} is already split into male/female")
    female_text, male_text = _clean(female_text), _clean(male_text)
    if female_text is None:
        raise WorkflowError(f"{string_key}: the female translation is empty, nothing to split")
    if male_text is None and base["translation"] is None:
        raise WorkflowError(f"{string_key}: no male translation (give one, or translate the shared text first)")
    split_note = "split into male/female versions" + (f"; {note}" if note else "")

    conn.execute("UPDATE strings SET gender='male', updated_at=datetime('now') WHERE id=?", (base["id"],))
    base = get_string(conn, string_key, "male")
    if male_text is not None and male_text != base["translation"]:
        _set_row_translation(conn, base, male_text, origin, by, machine_model, split_note)
    elif base["status"] == "approved":
        _log(conn, base, "needs_review", base["translation"], base["origin"], by, split_note)
        conn.execute("UPDATE strings SET status='needs_review', reviewer=NULL, updated_at=datetime('now')"
                     " WHERE id=?", (base["id"],))
    else:
        _log(conn, base, base["status"], base["translation"], base["origin"], by, split_note)

    values = [base[c] for c in _COPY_ON_SPLIT]
    cols = list(_COPY_ON_SPLIT) + ["gender", "translation", "status", "origin", "machine_model", "translator"]
    values += ["female", female_text, "draft", origin, machine_model if origin == "machine" else None,
               by if origin == "human" else None]
    conn.execute(f"INSERT INTO strings({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", values)
    female = get_string(conn, string_key, "female")
    conn.execute("INSERT INTO history(string_id, old_status, new_status, old_translation, new_translation,"
                 " origin, changed_by, note) VALUES (?,?,?,?,?,?,?,?)",
                 (female["id"], None, "draft", None, female_text, origin, by, split_note))
    return {"male": dict(get_string(conn, string_key, "male")), "female": dict(female)}


def merge_gender(conn, string_key: str, keep: str, by: str, note: str | None = None) -> dict:
    """Turn a male + female pair back into one shared translation (explicit command only).

    * keep='male' or 'female' chooses the text that survives
    * the male row becomes 'shared' and keeps its internal id
    * nothing historical is lost: the female row's history is moved to the shared row,
      and the female text is recorded there before the female row is removed
    * the result goes to 'needs_review' (a human has to look at it again)
    """
    if keep not in GENDERS:
        raise WorkflowError("keep must be male or female")
    male = get_string(conn, string_key, "male")
    female = get_string(conn, string_key, "female")
    if conn.execute("SELECT 1 FROM batch_items WHERE string_id=?", (female["id"],)).fetchone():
        raise WorkflowError(f"{string_key}: female version is referenced by batch records; merge refused")
    kept = male["translation"] if keep == "male" else female["translation"]
    merge_note = f"merged male/female into shared, kept {keep}" + (f"; {note}" if note else "")

    conn.execute("INSERT INTO history(string_id, old_status, new_status, old_translation, new_translation,"
                 " origin, changed_by, note) VALUES (?,?,?,?,?,?,?,?)",
                 (male["id"], female["status"], None, female["translation"], None, female["origin"], by,
                  f"female version removed by merge (internal id {female['id']})"))
    conn.execute("UPDATE history SET string_id=?, note=trim(coalesce(note,'') || ' [moved from female version, id "
                 + str(female["id"]) + "]') WHERE string_id=?", (male["id"], female["id"]))
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('variant_op', 'merge')")
    try:
        conn.execute("DELETE FROM strings WHERE id=?", (female["id"],))
    finally:
        conn.execute("DELETE FROM meta WHERE key='variant_op'")
    conn.execute("UPDATE strings SET gender='shared', updated_at=datetime('now') WHERE id=?", (male["id"],))
    shared = get_string(conn, string_key)
    new_origin = male["origin"] if keep == "male" else female["origin"]
    _log(conn, shared, "needs_review", kept, new_origin, by, merge_note)
    conn.execute("UPDATE strings SET translation=?, status='needs_review', origin=?, reviewer=NULL,"
                 " updated_at=datetime('now') WHERE id=?", (kept, new_origin, shared["id"]))
    return dict(get_string(conn, string_key))


# ---------------------------------------------------------------- active player gender
def get_active_gender(conn) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key='active_gender'").fetchone()
    return row[0] if row else None


def set_active_gender(conn, gender: str) -> str:
    if gender not in GENDERS:
        raise WorkflowError(f"active gender must be male or female, got {gender!r}")
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('active_gender', ?)", (gender,))
    return gender


def status_counts(conn) -> dict:
    counts = {s: 0 for s in STATUSES}
    for r in conn.execute("SELECT status, COUNT(*) n FROM strings GROUP BY status"):
        counts[r["status"]] = r["n"]
    machine = conn.execute(
        "SELECT COUNT(*) FROM strings WHERE origin='machine' AND status<>'approved'").fetchone()[0]
    counts["machine_unreviewed"] = machine
    counts["gendered_strings"] = conn.execute("SELECT COUNT(*) FROM strings WHERE gender='male'").fetchone()[0]
    counts["active_gender"] = get_active_gender(conn) or "not set"
    return counts


# ---------------------------------------------------------------- CSV
def import_csv(conn, csv_path: Path | str, by: str = "csv-import") -> dict:
    """Import strings (and optional translations) from a UTF-8 CSV.

    * new keys are inserted, existing keys get their source fields updated
    * a translation in the CSV that differs from the DB becomes a 'draft'
      (origin from the 'origin' column, default human)
    * 'needs_review' in the CSV is honoured; 'approved' is downgraded to 'needs_review'
    * an approved version is never changed: a different translation for it is skipped and
      counted in 'approved_protected' (a reviewer must 'reject' it first)
    * reviewer / approval columns are never read (listed in 'ignored_columns')
    * optional 'gender' column (shared/male/female) addresses one version of a split
      string; this import never splits or merges strings
    """
    stats = {"inserted": 0, "updated": 0, "source_changed": 0, "unchanged": 0,
             "translations_set": 0, "approved_downgraded": 0, "approved_protected": 0, "skipped": 0,
             "gender_mismatch": 0, "ignored_columns": []}
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if "string_key" not in (reader.fieldnames or []) or "source_text" not in reader.fieldnames:
            raise ValueError("CSV needs at least the columns string_key and source_text")
        stats["ignored_columns"] = sorted({"reviewer", "approved_by", "approved_at", "approval_id",
                                           "signature"} & set(reader.fieldnames))
        for rec in reader:
            key = _clean(rec.get("string_key"))
            source = rec.get("source_text")
            if key is None or source is None:
                stats["skipped"] += 1
                continue
            gender = (_clean(rec.get("gender")) or "").lower() or None
            if gender not in (None, "shared", "male", "female"):
                stats["skipped"] += 1
                continue
            extra = {k: v for k, v in rec.items() if k not in ("string_key", "source_text", "gender")}
            if gender in (None, "shared", "male"):     # source fields are shared by all versions
                result = upsert_source(conn, key, source, **extra)
                stats[result] += 1
            if not conn.execute("SELECT 1 FROM strings WHERE string_key=?", (key,)).fetchone():
                stats["gender_mismatch"] += 1          # female row for an unknown key
                continue
            gendered = is_gendered(conn, key)
            if gender is None:                         # old CSV: base row (male of a split string)
                row = get_string(conn, key)
            elif (gender == "shared") == gendered:     # shared on a split string, or male/female on a shared one
                stats["gender_mismatch"] += 1
                continue
            else:
                row = get_string(conn, key, None if gender == "shared" else gender)

            translation = _clean(rec.get("translation"))
            if translation is not None and translation != row["translation"] and row["status"] == "approved":
                stats["approved_protected"] += 1
                continue
            if translation is not None and translation != row["translation"]:
                origin = (_clean(rec.get("origin")) or "human").lower()
                origin = origin if origin in ("machine", "human") else "human"
                who = _clean(rec.get("translator")) or by
                _set_row_translation(conn, row, translation, origin, who,
                                     machine_model=_clean(rec.get("machine_model")))
                stats["translations_set"] += 1
                wanted = (_clean(rec.get("status")) or "draft").lower()
                if wanted == "approved":
                    stats["approved_downgraded"] += 1
                    wanted = "needs_review"
                if wanted == "needs_review":
                    row = conn.execute("SELECT * FROM strings WHERE id=?", (row["id"],)).fetchone()
                    _log(conn, row, "needs_review", row["translation"], row["origin"], who)
                    conn.execute("UPDATE strings SET status='needs_review', updated_at=datetime('now')"
                                 " WHERE id=?", (row["id"],))
    return stats


def export_csv(conn, csv_path: Path | str, status: str | None = None) -> int:
    """Export every version to CSV (UTF-8 with BOM so Excel on Windows shows Arabic correctly)."""
    columns = CSV_COLUMNS + ["gender"]
    sql = f"SELECT {','.join(columns)} FROM strings"
    params = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY string_key, CASE gender WHEN 'shared' THEN 0 WHEN 'male' THEN 1 ELSE 2 END"
    rows = conn.execute(sql, params).fetchall()
    csv_path = Path(csv_path)
    ensure_dir(csv_path.parent)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(columns)
        for r in rows:
            w.writerow(["" if r[c] is None else r[c] for c in columns])
    return len(rows)
