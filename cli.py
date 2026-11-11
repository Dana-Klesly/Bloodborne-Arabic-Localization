"""Bloodborne-Arabic-Localization command line.

Run from the project root with the project venv, e.g.:
  .venv\\Scripts\\python cli.py init
  .venv\\Scripts\\python cli.py glossary-load
  .venv\\Scripts\\python cli.py import tests\\samples\\samples_original.csv
  .venv\\Scripts\\python cli.py validate
  .venv\\Scripts\\python cli.py preview --status draft
  .venv\\Scripts\\python cli.py status

Nothing here reads or writes game files.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common.paths import DEFAULT_DB, GLOSSARY_CSV, LOGS_DIR  # noqa: E402
from database import db  # noqa: E402


def _out(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_init(a):
    with db.open_db(a.db):
        pass
    print(f"database ready: {a.db}")


def cmd_glossary_load(a):
    from glossary.glossary import load_into_db
    with db.open_db(a.db) as conn:
        n = load_into_db(conn, a.csv)
    print(f"glossary: {n} terms loaded from {a.csv}")


def cmd_import(a):
    with db.open_db(a.db) as conn:
        _out(db.import_csv(conn, a.csv, by=a.by))


def cmd_export(a):
    with db.open_db(a.db) as conn:
        n = db.export_csv(conn, a.csv, status=a.status)
    print(f"exported {n} rows to {a.csv}")


def cmd_status(a):
    with db.open_db(a.db) as conn:
        _out(db.status_counts(conn))


def cmd_set(a):
    with db.open_db(a.db) as conn:
        print(db.set_translation(conn, a.key, a.text, "human", a.by))


def cmd_review(a):
    with db.open_db(a.db) as conn:
        db.mark_for_review(conn, a.key, a.by)
    print("needs_review")


def cmd_approve(a):
    from validation.validator import errors_for
    with db.open_db(a.db) as conn:
        db.approve(conn, a.key, a.reviewer, errors_for(conn, a.key, a.font))
    print("approved")


def cmd_reject(a):
    with db.open_db(a.db) as conn:
        db.reject(conn, a.key, a.by, a.note)
    print("draft")


def cmd_validate(a):
    from validation import validator
    with db.open_db(a.db) as conn:
        summary = validator.run(conn, font_path=a.font, statuses=a.status)
    _out(summary)
    return 1 if summary["error"] and a.strict else 0


def cmd_font_check(a):
    from fonts.coverage import coverage
    from fonts.preview import find_font
    _out(coverage(find_font(a.font)))


def cmd_preview(a):
    from fonts.preview import PreviewItem, render
    items = []
    if a.text:
        items.append(PreviewItem("text", a.english or "", a.text, a.max_px))
    else:
        with db.open_db(a.db) as conn:
            sql = "SELECT string_key, status, source_text, translation FROM strings" \
                  " WHERE translation IS NOT NULL"
            params = []
            if a.status:
                sql += f" AND status IN ({','.join('?' * len(a.status))})"
                params += a.status
            if a.key:
                sql += f" AND string_key IN ({','.join('?' * len(a.key))})"
                params += a.key
            for r in conn.execute(sql + " ORDER BY string_key LIMIT ?", params + [a.limit]):
                items.append(PreviewItem(f"{r['string_key']} [{r['status']}]",
                                         r["source_text"], r["translation"], a.max_px))
    if not items:
        print("nothing to preview")
        return 1
    _out(render(items, a.out, font_path=a.font, size=a.size, wrap=a.wrap))
    return 0


def cmd_sources_import(a):
    from translation import batch
    with db.open_db(a.db) as conn:
        _out(batch.import_sources(conn, a.csv))


def cmd_batch_export(a):
    from translation import batch
    with db.open_db(a.db) as conn:
        res = batch.export_batch(conn, limit=a.limit, types=a.type, statuses=tuple(a.status),
                                 include_open=a.include_open)
    if not res["rows"]:
        print("nothing to export (no matching strings, or they are already in an open batch)")
        return 0
    _out(res)
    return 0


def cmd_batch_import(a):
    from translation import batch
    with db.open_db(a.db) as conn:
        _out(batch.import_batch(conn, a.csv, translator=a.translator,
                                allow_overwrite_approved=a.allow_overwrite_approved,
                                overwrite_drafts=a.overwrite_drafts))


def cmd_batch_list(a):
    from translation import batch
    with db.open_db(a.db) as conn:
        _out(batch.list_batches(conn))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=str(DEFAULT_DB), help="SQLite file (default: database/localization.db)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create the database").set_defaults(fn=cmd_init)

    s = sub.add_parser("glossary-load", help="load glossary.csv into the DB")
    s.add_argument("--csv", default=str(GLOSSARY_CSV))
    s.set_defaults(fn=cmd_glossary_load)

    s = sub.add_parser("import", help="import strings/translations from CSV")
    s.add_argument("csv")
    s.add_argument("--by", default="csv-import")
    s.set_defaults(fn=cmd_import)

    s = sub.add_parser("export", help="export strings to CSV")
    s.add_argument("csv")
    s.add_argument("--status", choices=db.STATUSES)
    s.set_defaults(fn=cmd_export)

    sub.add_parser("status", help="count strings per status").set_defaults(fn=cmd_status)

    s = sub.add_parser("set", help="set a human translation (-> draft)")
    s.add_argument("key"); s.add_argument("text"); s.add_argument("--by", required=True)
    s.set_defaults(fn=cmd_set)

    s = sub.add_parser("review", help="draft -> needs_review")
    s.add_argument("key"); s.add_argument("--by", required=True)
    s.set_defaults(fn=cmd_review)

    s = sub.add_parser("approve", help="needs_review -> approved (human reviewer, no validation errors)")
    s.add_argument("key"); s.add_argument("--reviewer", required=True); s.add_argument("--font")
    s.set_defaults(fn=cmd_approve)

    s = sub.add_parser("reject", help="needs_review/approved -> draft")
    s.add_argument("key"); s.add_argument("--by", required=True); s.add_argument("--note")
    s.set_defaults(fn=cmd_reject)

    s = sub.add_parser("validate", help="run all checks, write logs/validation_*.csv")
    s.add_argument("--font", help="also check glyph coverage of this font")
    s.add_argument("--status", nargs="*", choices=db.STATUSES)
    s.add_argument("--strict", action="store_true", help="exit code 1 if any error")
    s.set_defaults(fn=cmd_validate)

    s = sub.add_parser("font-check", help="Arabic coverage of a font")
    s.add_argument("--font")
    s.set_defaults(fn=cmd_font_check)

    s = sub.add_parser("preview", help="render a PNG preview")
    s.add_argument("--text", help="Arabic text to render instead of DB rows")
    s.add_argument("--english")
    s.add_argument("--status", nargs="*", choices=db.STATUSES)
    s.add_argument("--key", nargs="*")
    s.add_argument("--limit", type=int, default=30)
    s.add_argument("--font")
    s.add_argument("--size", type=int, default=30)
    s.add_argument("--max-px", type=int, help="simulated text box width in pixels")
    s.add_argument("--wrap", action="store_true", help="word-wrap to --max-px")
    s.add_argument("--out", default=str(LOGS_DIR / "preview.png"))
    s.set_defaults(fn=cmd_preview)

    s = sub.add_parser("sources-import", help="import English source strings (id, source, context, type)")
    s.add_argument("csv")
    s.set_defaults(fn=cmd_sources_import)

    s = sub.add_parser("batch-export", help="write the next batch CSV for the translator")
    s.add_argument("--limit", type=int, default=40)
    s.add_argument("--type", nargs="*", help="dialogue ui item_name item_desc system place")
    s.add_argument("--status", nargs="*", default=["pending"], choices=db.STATUSES)
    s.add_argument("--include-open", action="store_true", help="also include rows already in an open batch")
    s.set_defaults(fn=cmd_batch_export)

    s = sub.add_parser("batch-import", help="import a translated batch CSV (never approves)")
    s.add_argument("csv")
    s.add_argument("--translator", default="claude")
    s.add_argument("--allow-overwrite-approved", action="store_true",
                   help="replace approved translations (they go back to review)")
    s.add_argument("--overwrite-drafts", action="store_true",
                   help="replace human draft/needs_review translations")
    s.set_defaults(fn=cmd_batch_import)

    sub.add_parser("batch-list", help="list batches and their results").set_defaults(fn=cmd_batch_list)

    # Windows consoles often default to a non-UTF-8 code page; Arabic output would crash.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    a = p.parse_args(argv)
    from translation.batch import BatchError
    try:
        return a.fn(a) or 0
    except BatchError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        for p in exc.problems:
            print(f"  - {p}", file=sys.stderr)
        return 2
    except (db.WorkflowError, KeyError, ValueError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
