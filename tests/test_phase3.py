"""Phase 3: dry runs, batch cancel, re-export with current translations, approval columns ignored,
type length hints, review sheet, glossary female suggestions, and an end-to-end CLI run.

All data is our own invented sample text. Every test works in a temporary folder with its own
SQLite file; report folders of the modules are pointed at that folder.
"""
import contextlib
import csv
import hashlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.helpers import read_csv
from common.paths import GLOSSARY_CSV, LOGS_DIR, SAMPLES_DIR, TRANSLATION_DIR
from database import db
from glossary.glossary import load_into_db, read_csv as read_glossary
from translation import batch
from validation import review_report, validator

SOURCES = SAMPLES_DIR / "batch_sources.csv"
TRANSLATIONS = {r["id"]: r["translation"] for r in read_csv(SAMPLES_DIR / "batch_translations.csv")}
REC, REST = "demo:system:received", "demo:ui:rest"
MALE, FEMALE = "تسلّمتَ %s.", "تسلّمتِ %s."


def file_sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_csv(path, rows, fieldnames=None, encoding="utf-8"):
    fieldnames = fieldnames or list(rows[0].keys())
    with open(path, "w", encoding=encoding, newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    return path


class Base(unittest.TestCase):
    """A file-backed temp DB that is opened and closed around each operation, so the file
    itself can be compared byte by byte."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "p3.db"
        with self.conn() as c:
            load_into_db(c, GLOSSARY_CSV)
            batch.import_sources(c, SOURCES, report_dir=self.dir)

    def tearDown(self):
        self.tmp.cleanup()

    @contextlib.contextmanager
    def conn(self):
        with db.open_db(self.path) as c:
            yield c

    def export(self, **kw):
        kw.setdefault("out_dir", self.dir / "out")
        with self.conn() as c:
            return batch.export_batch(c, **kw)

    def fill(self, exported, translations=None, female=None, edit=None, name="ret.csv"):
        translations = TRANSLATIONS if translations is None else translations
        rows = read_csv(exported)
        for r in rows:
            r["translation"] = translations.get(r["id"], "")
            if female and r["id"] in female:
                r["translation_female"] = female[r["id"]]
        if edit:
            edit(rows)
        return write_csv(self.dir / name, rows)

    def imp(self, path, **kw):
        with self.conn() as c:
            return batch.import_batch(c, path, report_dir=self.dir, **kw)

    def counts(self):
        with self.conn() as c:
            return db.status_counts(c)

    def row(self, key, gender=None):
        with self.conn() as c:
            return dict(db.get_string(c, key, gender))


# --------------------------------------------------------------------------- sources import
class SourcesImportTests(Base):
    def test_dry_run_reports_but_leaves_the_file_identical(self):
        src = write_csv(self.dir / "s.csv", [
            {"id": "demo:ui:new_btn", "type": "ui", "source": "Abandon", "context": "", "max_length": "16"},
            {"id": REST, "type": "ui", "source": "Rest now", "context": "", "max_length": "16"}])
        before = file_sha(self.path)
        with self.conn() as c:
            stats = batch.import_sources(c, src, report_dir=self.dir, dry_run=True)
        self.assertEqual(file_sha(self.path), before, "dry run must not change the database file")
        self.assertEqual((stats["inserted"], stats["source_changed"], stats["dry_run"]), (1, 1, True))
        self.assertTrue(stats["report"].endswith("_dryrun.csv"))
        with self.conn() as c:
            real = batch.import_sources(c, src, report_dir=self.dir)
        self.assertEqual((real["inserted"], real["source_changed"]), (1, 1), "dry run predicts the real run")
        self.assertNotEqual(file_sha(self.path), before)

    def test_dry_run_of_refused_file_changes_nothing(self):
        bad = write_csv(self.dir / "bad.csv", [{"id": "x:1", "source": "A"}, {"id": "x:1", "source": "B"}])
        before = file_sha(self.path)
        with self.conn() as c, self.assertRaises(batch.BatchError):
            batch.import_sources(c, bad, report_dir=self.dir, dry_run=True)
        self.assertEqual(file_sha(self.path), before)

    def test_dry_run_keeps_the_callers_own_pending_changes(self):
        src = write_csv(self.dir / "s.csv", [{"id": "demo:ui:new_btn", "source": "Abandon"}])
        with self.conn() as c:
            db.set_translation(c, REST, "استرِح", "human", "Dana")          # caller's change, not committed yet
            batch.import_sources(c, src, report_dir=self.dir, dry_run=True)
            self.assertEqual(db.get_string(c, REST)["translation"], "استرِح")
            self.assertIsNone(c.execute("SELECT 1 FROM strings WHERE string_key='demo:ui:new_btn'").fetchone())
        self.assertEqual(self.row(REST)["translation"], "استرِح")

    def test_extra_source_columns_are_stored(self):
        src = write_csv(self.dir / "s.csv", [{"id": "demo:item_desc:extra", "type": "item_desc",
                                              "source": "Line one.\nLine two.", "max_line_length": "30",
                                              "source_file": "demo/items.txt", "fmg_name": "DemoItemInfo",
                                              "entry_id": "1234"}])
        with self.conn() as c:
            batch.import_sources(c, src, report_dir=self.dir)
        r = self.row("demo:item_desc:extra")
        self.assertEqual((r["max_line_length"], r["source_file"], r["fmg_name"], r["entry_id"]),
                         (30, "demo/items.txt", "DemoItemInfo", 1234))

    def test_number_columns_must_be_numbers(self):
        for col in ("max_length", "max_line_length", "entry_id"):
            bad = write_csv(self.dir / f"{col}.csv", [{"id": "x:1", "source": "A", col: "ten"}])
            with self.conn() as c, self.assertRaises(batch.BatchError) as cm:
                batch.import_sources(c, bad, report_dir=self.dir)
            self.assertIn(col, " ".join(cm.exception.problems))

    def test_empty_file_refused(self):
        p = self.dir / "empty.csv"
        p.write_text("id,source\n", encoding="utf-8")
        with self.conn() as c, self.assertRaises(batch.BatchError):
            batch.import_sources(c, p, report_dir=self.dir)


# --------------------------------------------------------------------------- export
class ExportTests(Base):
    def test_columns_and_empty_information_for_new_strings(self):
        res = self.export(limit=20)
        rows = read_csv(res["file"])
        self.assertEqual(list(rows[0].keys()), batch.BATCH_COLUMNS)
        self.assertEqual(len(rows), 20)
        self.assertTrue(all(r["current_translation"] == "" and r["issues"] == "" for r in rows))

    def test_limit_must_be_positive(self):
        with self.conn() as c:
            for bad in (0, -5):
                with self.assertRaises(batch.BatchError):
                    batch.export_batch(c, limit=bad, out_dir=self.dir / "out")

    def test_drafts_are_sent_again_with_their_translation_and_errors(self):
        self.imp(self.fill(self.export(limit=40)["file"]))
        self.assertEqual(self.row("demo:system:need_key")["status"], "draft")   # sample has a missing code
        res = self.export(statuses=("draft",), limit=40)
        rows = {r["id"]: r for r in read_csv(res["file"])}
        self.assertEqual(set(rows), {"demo:system:need_key"})
        r = rows["demo:system:need_key"]
        self.assertEqual(r["current_translation"], TRANSLATIONS["demo:system:need_key"])
        self.assertIn("error:placeholders", r["issues"])
        self.assertEqual(r["translation"], "", "the column to fill stays empty")

    def test_split_string_carries_both_current_versions(self):
        self.imp(self.fill(self.export(limit=40)["file"], female={REC: FEMALE},
                           translations=dict(TRANSLATIONS, **{REC: MALE})))
        res = self.export(statuses=("needs_review",), limit=40, include_open=True)
        r = {x["id"]: x for x in read_csv(res["file"])}[REC]
        self.assertEqual((r["gender"], r["current_translation"], r["current_translation_female"]),
                         ("male+female", MALE, FEMALE))

    def test_information_columns_are_never_imported(self):
        res = self.export(limit=40)

        def edit(rows):
            for r in rows:
                r["current_translation"] = "نص مزروع"
                r["current_translation_female"] = "نص مزروع"
                r["issues"] = "error:fake"
        self.imp(self.fill(res["file"], edit=edit))
        with self.conn() as c:
            self.assertFalse(c.execute("SELECT 1 FROM strings WHERE translation LIKE '%مزروع%'").fetchone())
            self.assertFalse(c.execute("SELECT 1 FROM strings WHERE gender='female'").fetchone())


# --------------------------------------------------------------------------- import
class ImportTests(Base):
    def test_dry_run_leaves_file_identical_and_predicts_result(self):
        res = self.export(limit=40)
        ret = self.fill(res["file"], female={REC: FEMALE}, translations=dict(TRANSLATIONS, **{REC: MALE}))
        before = file_sha(self.path)
        dry = self.imp(ret, dry_run=True)
        self.assertEqual(file_sha(self.path), before, "dry run must not change the database file")
        self.assertTrue(dry["dry_run"] and dry["report"].endswith("_report_dryrun.csv"))
        real = self.imp(ret)
        for k in ("accepted_review", "accepted_draft", "rejected", "skipped", "split", "errors", "approved_by_import"):
            self.assertEqual(dry[k], real[k], k)
        self.assertEqual(real["approved_by_import"], 0)
        self.assertNotEqual(file_sha(self.path), before)

    def test_dry_run_of_refused_file_changes_nothing(self):
        res = self.export(limit=40)
        ret = self.fill(res["file"], edit=lambda rows: rows.append(dict(rows[0])))      # duplicate id
        before = file_sha(self.path)
        with self.assertRaises(batch.BatchError):
            self.imp(ret, dry_run=True)
        self.assertEqual(file_sha(self.path), before)

    def test_approval_columns_in_file_are_ignored(self):
        res = self.export(limit=40)

        def edit(rows):
            for r in rows:
                r.update(status="approved", reviewer="Dana", approved="yes", approved_by="Dana", approval_id="x",
                         signature="forged", origin="human", approved_at="2026-01-01T00:00:00Z")
        stats = self.imp(self.fill(res["file"], edit=edit))
        self.assertEqual(self.counts()["approved"], 0)
        self.assertEqual(stats["approved_by_import"], 0)
        self.assertEqual(set(stats["ignored_columns"]), batch.APPROVAL_COLUMNS)
        with self.conn() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM strings WHERE reviewer IS NOT NULL").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM strings WHERE origin='human'").fetchone()[0], 0)
        self.assertIn("ignored_columns", {r["check"] for r in read_csv(stats["report"])})

    def test_type_hint_warns_but_does_not_block_review(self):
        res = self.export(limit=40)
        long_ui = "هل تريد حقاً أن ترمي {item} إلى الأبد؟"                 # confirm_discard: ui, no max_length
        stats = self.imp(self.fill(res["file"], dict(TRANSLATIONS, **{"demo:ui:confirm_discard": long_ui})))
        self.assertEqual(self.row("demo:ui:confirm_discard")["status"], "needs_review")
        hits = [r for r in read_csv(stats["report"]) if r["check"] == "length_type"]
        self.assertEqual([(r["id"], r["severity"]) for r in hits], [("demo:ui:confirm_discard", "warning")])


class TypeLengthHintTests(unittest.TestCase):
    def test_rules(self):
        from validation.checks import TYPE_LENGTH_HINTS, check_type_length
        ui = TYPE_LENGTH_HINTS["ui"]
        self.assertEqual(check_type_length("ا" * ui, "ui", None), [], "at the hint is fine")
        self.assertEqual([i.check for i in check_type_length("ا" * (ui + 1), "ui", None)], ["length_type"])
        self.assertEqual(check_type_length("ا" * (ui + 1), "ui", 40), [], "a real max_length wins")
        self.assertEqual(check_type_length("ا" * 500, "dialogue", None), [], "no hint for dialogue")
        self.assertEqual(check_type_length("ا" * 500, None, None), [])


# --------------------------------------------------------------------------- cancel
class CancelTests(Base):
    def snapshot(self):
        with self.conn() as c:
            return (sorted(tuple(r) for r in c.execute("SELECT * FROM strings")),
                    sorted(tuple(r) for r in c.execute("SELECT * FROM history")))

    def test_cancel_frees_rows_and_changes_no_translation(self):
        res = self.export(limit=5)
        before = self.snapshot()
        with self.conn() as c:
            out = batch.cancel_batch(c, res["batch_id"], "Dana", "file lost")
        self.assertEqual((out["cancelled_rows"], out["kept_rows"]), (5, 0))
        self.assertEqual(self.snapshot(), before, "cancel must not touch strings or history")
        with self.conn() as c:
            items = c.execute("SELECT result FROM batch_items WHERE batch_id=?", (res["batch_id"],)).fetchall()
            note = c.execute("SELECT note FROM batches WHERE batch_id=?", (res["batch_id"],)).fetchone()[0]
        self.assertEqual({r[0] for r in items}, {batch.CANCELLED})
        self.assertEqual(len(items), 5, "nothing deleted")
        self.assertIn("by Dana", note)
        self.assertIn("file lost", note)
        again = self.export(limit=5)
        self.assertEqual(sorted(r["id"] for r in read_csv(again["file"])),
                         sorted(r["id"] for r in read_csv(res["file"])), "rows can be exported again")

    def test_cancel_errors(self):
        res = self.export(limit=3)
        with self.conn() as c:
            with self.assertRaises(batch.BatchError):
                batch.cancel_batch(c, "batch_unknown", "Dana")
            with self.assertRaises(batch.BatchError):
                batch.cancel_batch(c, res["batch_id"], "  ")
            batch.cancel_batch(c, res["batch_id"], "Dana")
            with self.assertRaises(batch.BatchError):
                batch.cancel_batch(c, res["batch_id"], "Dana")          # nothing open any more

    def test_cancelled_batch_file_is_refused(self):
        res = self.export(limit=5)
        ret = self.fill(res["file"])
        with self.conn() as c:
            batch.cancel_batch(c, res["batch_id"], "Dana")
        before = file_sha(self.path)
        with self.assertRaises(batch.BatchError) as cm:
            self.imp(ret)
        self.assertIn("cancelled", " ".join(cm.exception.problems))
        self.assertEqual(file_sha(self.path), before)

    def test_partial_import_then_cancel_keeps_imported_rows(self):
        res = self.export(limit=40)
        rows = read_csv(res["file"])
        first_two = {r["id"] for r in rows[:2]}
        ret = self.fill(res["file"], {k: v for k, v in TRANSLATIONS.items() if k in first_two})
        self.imp(ret)                                                   # 2 accepted, the rest skipped
        with self.conn() as c:
            skipped_before = c.execute("SELECT COUNT(*) FROM batch_items WHERE batch_id=? AND result='skipped'",
                                       (res["batch_id"],)).fetchone()[0]
            # skipped rows are not 'open' (result is set), so only rows with no result are cancelled
            c.execute("UPDATE batch_items SET result=NULL WHERE batch_id=? AND result='skipped'", (res["batch_id"],))
            out = batch.cancel_batch(c, res["batch_id"], "Dana")
        self.assertEqual(out["cancelled_rows"], skipped_before)
        full = self.fill(res["file"], name="full.csv")
        stats = self.imp(full)
        rej = {r["id"]: r["check"] for r in read_csv(stats["report"]) if r["result"] == "rejected"}
        self.assertEqual(set(rej.values()), {"batch_cancelled"})
        self.assertEqual(set(rej), {r["id"] for r in rows} - first_two)
        with self.conn() as c:
            results = {r[0] for r in c.execute("SELECT result FROM batch_items WHERE batch_id=? AND string_key "
                                               "NOT IN (%s)" % ",".join("?" * 2), [res["batch_id"], *first_two])}
        self.assertEqual(results, {batch.CANCELLED}, "cancelled rows stay cancelled")


# --------------------------------------------------------------------------- generic import (rule 11)
class GenericImportTests(Base):
    def test_import_csv_never_changes_an_approved_version_and_ignores_reviewer(self):
        with self.conn() as c:
            db.set_translation(c, REST, "استراحة", "human", "Dana")
            db.mark_for_review(c, REST, "Dana")
            db.approve(c, REST, "Dana")
            hist = c.execute("SELECT COUNT(*) FROM history").fetchone()[0]
        p = write_csv(self.dir / "g.csv", [
            {"string_key": REST, "source_text": "Rest", "translation": "اِسترِح", "status": "approved",
             "reviewer": "Mallory", "approval_id": "x", "signature": "forged"}])
        with self.conn() as c:
            stats = db.import_csv(c, p)
        self.assertEqual(stats["approved_protected"], 1)
        self.assertEqual(set(stats["ignored_columns"]), {"reviewer", "approval_id", "signature"})
        r = self.row(REST)
        self.assertEqual((r["status"], r["translation"], r["reviewer"]), ("approved", "استراحة", "Dana"))
        with self.conn() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM history").fetchone()[0], hist)

    def test_import_csv_never_sets_approved_or_reviewer(self):
        p = write_csv(self.dir / "g.csv", [
            {"string_key": "demo:ui:travel", "source_text": "Travel", "translation": "ارتحال",
             "status": "approved", "reviewer": "Dana"}])
        with self.conn() as c:
            stats = db.import_csv(c, p)
        r = self.row("demo:ui:travel")
        self.assertEqual((r["status"], r["reviewer"], stats["approved_downgraded"]), ("needs_review", None, 1))


# --------------------------------------------------------------------------- review sheet
class ReviewReportTests(Base):
    def test_sheet_is_read_only_and_complete(self):
        self.imp(self.fill(self.export(limit=40)["file"], female={REC: FEMALE},
                           translations=dict(TRANSLATIONS, **{REC: MALE})))
        before = file_sha(self.path)
        with self.conn() as c:
            out = review_report.export_review(c, self.dir / "review.csv", statuses=["needs_review", "draft"])
        self.assertEqual(file_sha(self.path), before, "review sheet must not write to the database")
        rows = read_csv(out["file"])
        self.assertEqual(list(rows[0].keys()), review_report.REVIEW_COLUMNS)
        by = {(r["string_key"], r["gender"]): r for r in rows}
        self.assertEqual(by[(REC, "male")]["other_version"], FEMALE)
        self.assertEqual(by[(REC, "female")]["other_version"], MALE)
        self.assertIn("placeholders", by[("demo:system:need_key", "shared")]["errors"])
        self.assertEqual(out["with_errors"], 1)
        self.assertEqual(self.counts()["approved"], 0)

    def test_default_status_is_needs_review_only(self):
        self.imp(self.fill(self.export(limit=40)["file"]))
        with self.conn() as c:
            out = review_report.export_review(c, self.dir / "r.csv")
        self.assertEqual({r["status"] for r in read_csv(out["file"])}, {"needs_review"})


# --------------------------------------------------------------------------- glossary
class GlossaryFemaleSuggestionTests(unittest.TestCase):
    def test_project_glossary_has_female_suggestions_still_proposed(self):
        terms = {t.term_en: t for t in read_glossary(GLOSSARY_CSV)}
        self.assertEqual(terms["Hunter"].female_ar, "الصيادة")
        self.assertEqual(terms["Good hunter"].female_ar, "الصيادة الطيبة")
        for k in ("Hunter", "Good hunter"):
            self.assertEqual(terms[k].status, "proposed", "suggestions are not approved automatically")
        self.assertEqual(terms["Hunter"].approved_ar, "الصياد", "the male/shared form is unchanged")


# --------------------------------------------------------------------------- CLI end to end
def _tree(path: Path):
    if not path.exists():
        return None
    return sorted((p.relative_to(path).as_posix(), p.stat().st_size, p.stat().st_mtime_ns)
                  for p in path.rglob("*") if "__pycache__" not in p.parts)


class CliPhase3Tests(unittest.TestCase):
    """The whole Phase 3 flow through cli.py with a 20-line trial batch, in a temp folder only."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.real = {p: _tree(p) for p in (LOGS_DIR, TRANSLATION_DIR / "batches")}
        self._saved = (batch.LOGS_DIR, batch.BATCH_OUT, batch.BATCH_IN, review_report.LOGS_DIR, validator.LOGS_DIR)
        batch.LOGS_DIR, batch.BATCH_OUT, batch.BATCH_IN = self.d / "logs", self.d / "out", self.d / "in"
        review_report.LOGS_DIR = validator.LOGS_DIR = self.d / "logs"

    def tearDown(self):
        batch.LOGS_DIR, batch.BATCH_OUT, batch.BATCH_IN, review_report.LOGS_DIR, validator.LOGS_DIR = self._saved
        self.tmp.cleanup()
        for p, snap in self.real.items():
            self.assertEqual(_tree(p), snap, f"real folder {p} must not change")

    def cli(self, *args):
        import cli
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["--db", str(self.d / "c.db"), *args])
        return code, out.getvalue()

    def test_full_flow(self):
        dbp = self.d / "c.db"
        self.assertEqual(self.cli("init")[0], 0)
        self.assertEqual(self.cli("glossary-load")[0], 0)
        h = file_sha(dbp)
        self.assertEqual(self.cli("sources-import", str(SOURCES), "--dry-run")[0], 0)
        self.assertEqual(file_sha(dbp), h, "sources-import --dry-run leaves the DB as it was")
        self.assertEqual(self.cli("sources-import", str(SOURCES))[0], 0)

        # a batch that is lost and cancelled, then the 20-line trial batch
        self.assertEqual(self.cli("batch-export", "--limit", "3")[0], 0)
        lost = next((self.d / "out").glob("batch_*.csv"))
        lost_id = lost.stem
        self.assertEqual(self.cli("batch-cancel", lost_id, "--by", "Dana", "--reason", "trial")[0], 0)
        self.assertEqual(self.cli("batch-cancel", lost_id, "--by", "Dana")[0], 2, "second cancel refused")
        shutil.move(str(lost), str(self.d / "lost.csv"))
        self.assertEqual(self.cli("batch-export", "--limit", "20")[0], 0)
        exported = next((self.d / "out").glob("batch_*.csv"))
        rows = read_csv(exported)
        self.assertEqual(len(rows), 20)
        for r in rows:
            r["translation"] = TRANSLATIONS[r["id"]]
        ret = write_csv(self.d / "in" / exported.name, rows, batch.BATCH_COLUMNS)

        h = file_sha(dbp)
        self.assertEqual(self.cli("batch-import", str(ret), "--dry-run")[0], 0)
        self.assertEqual(file_sha(dbp), h, "batch-import --dry-run leaves the DB as it was")
        self.assertEqual(self.cli("batch-import", str(ret))[0], 0)
        self.assertEqual(self.cli("batch-import", str(self.d / "lost.csv"))[0], 2, "cancelled batch refused")

        code, out = self.cli("review-export")
        self.assertEqual(code, 0)
        self.assertTrue(list((self.d / "logs").glob("review_*.csv")))
        self.assertEqual(self.cli("validate")[0], 0)
        self.assertEqual(self.cli("batch-list")[0], 0)
        with self.assertRaises(SystemExit):                 # argparse refuses --limit 0
            self.cli("batch-export", "--limit", "0")
        self.assertTrue(list((self.d / "logs").glob("validation_*.csv")), "validate wrote into the temp folder")
        with db.open_db(dbp) as conn:
            counts = db.status_counts(conn)
        self.assertEqual(counts["approved"], 0, "nothing approved by any import")
        self.assertEqual(counts["needs_review"] + counts["draft"], 20)

    def test_removed_overwrite_approved_option(self):
        self.assertEqual(self.cli("init")[0], 0)
        with self.assertRaises(SystemExit):
            self.cli("batch-import", "x.csv", "--allow-overwrite-approved")


if __name__ == "__main__":
    unittest.main()
