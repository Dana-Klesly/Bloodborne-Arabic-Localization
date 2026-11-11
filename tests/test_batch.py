"""Batch translation workflow: sources import, batch export, batch import.

Uses only our own invented sample texts (tests/samples/batch_*.csv).
"""
import csv
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.helpers import read_csv
from common.paths import GLOSSARY_CSV, SAMPLES_DIR
from common.placeholders import find_tokens
from database import db
from glossary.glossary import load_into_db
from translation import batch

SOURCES = SAMPLES_DIR / "batch_sources.csv"
TRANSLATIONS = {r["id"]: r["translation"] for r in read_csv(SAMPLES_DIR / "batch_translations.csv")}


class BatchTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.conn = db.connect(self.dir / "t.db")
        db.init_db(self.conn)
        load_into_db(self.conn, GLOSSARY_CSV)
        self.src_stats = batch.import_sources(self.conn, SOURCES, report_dir=self.dir)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    # helpers ------------------------------------------------------------
    def export(self, **kw):
        kw.setdefault("out_dir", self.dir / "out")
        return batch.export_batch(self.conn, **kw)

    def fill(self, exported_file, translations=None, edit=None, encoding="utf-8", name="returned.csv"):
        """Simulate the translator: fill the translation column of an exported batch."""
        translations = TRANSLATIONS if translations is None else translations
        rows = read_csv(exported_file)
        for r in rows:
            r["translation"] = translations.get(r["id"], "")
        if edit:
            edit(rows)
        out = self.dir / name
        with open(out, "w", encoding=encoding, newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        return out

    def do_import(self, path, **kw):
        return batch.import_batch(self.conn, path, report_dir=self.dir, **kw)

    def status(self, key):
        return db.get_string(self.conn, key)["status"]


class SourcesImportTests(BatchTestBase):
    def test_imported_as_pending_with_types(self):
        self.assertEqual(self.src_stats["inserted"], 20)
        self.assertEqual(db.status_counts(self.conn)["pending"], 20)
        # Arabic type names are mapped
        self.assertEqual(db.get_string(self.conn, "demo:ui:travel")["category"], "ui")
        self.assertEqual(db.get_string(self.conn, "demo:dialogue:keeper_02")["category"], "dialogue")
        self.assertEqual(db.get_string(self.conn, "demo:item_desc:ash_lantern")["category"], "item_desc")

    def test_arabic_and_multiline_kept(self):
        r = db.get_string(self.conn, "demo:item_desc:ash_lantern")
        self.assertEqual(r["source_text"].count("\n"), 1)
        self.assertEqual(r["context"], "وصف أداة")

    def test_duplicate_source_warned(self):
        rep = read_csv(self.src_stats["report"])
        dup = {r["id"] for r in rep if r["check"] == "duplicate_source"}
        self.assertEqual(dup, {"demo:ui:rest", "demo:ui:rest_dup"})

    def _bad_sources(self, rows):
        p = self.dir / "bad_src.csv"
        with open(p, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["id", "source", "context", "type"])
            w.writerows(rows)
        return p

    def test_duplicate_id_refuses_whole_file(self):
        p = self._bad_sources([["x:1", "One", "", "ui"], ["x:2", "Two", "", "ui"], ["x:1", "Again", "", "ui"]])
        with self.assertRaises(batch.BatchError) as cm:
            batch.import_sources(self.conn, p, report_dir=self.dir)
        self.assertIn("duplicate id", " ".join(cm.exception.problems))
        self.assertIsNone(self.conn.execute("SELECT 1 FROM strings WHERE string_key='x:2'").fetchone(),
                          "nothing may be written when the file is refused")

    def test_invalid_id_type_and_empty_source_refused(self):
        for row, word in ((["bad id!", "Text", "", "ui"], "invalid id"),
                          (["x:3", "Text", "", "poem"], "unknown type"),
                          (["x:4", "  ", "", "ui"], "empty source")):
            with self.subTest(word):
                with self.assertRaises(batch.BatchError) as cm:
                    batch.import_sources(self.conn, self._bad_sources([row]), report_dir=self.dir)
                self.assertIn(word, " ".join(cm.exception.problems))

    def test_changed_source_warned(self):
        p = self._bad_sources([["demo:ui:rest", "Rest here", "", "ui"]])
        stats = batch.import_sources(self.conn, p, report_dir=self.dir)
        self.assertEqual(stats["source_changed"], 1)
        self.assertIn("source_changed", {r["check"] for r in read_csv(stats["report"])})

    def test_non_utf8_refused(self):
        p = self.dir / "cp1256.csv"
        p.write_bytes("id,source,context,type\nx:9,Rest,زر,ui\n".encode("cp1256"))
        with self.assertRaises(batch.BatchError):
            batch.import_sources(self.conn, p, report_dir=self.dir)


class ExportTests(BatchTestBase):
    def test_export_file_keeps_utf8_ids_placeholders(self):
        res = self.export(limit=20)
        self.assertEqual(res["rows"], 20)
        raw = Path(res["file"]).read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM for Excel")
        rows = read_csv(res["file"])
        self.assertEqual(list(rows[0].keys()), batch.BATCH_COLUMNS)
        by_id = {r["id"]: r for r in rows}
        src = {r["id"]: r["source"] for r in read_csv(SOURCES)}
        for rid, r in by_id.items():
            self.assertEqual(r["source"], src[rid], "source text must be exported byte-identical")
            self.assertEqual(r["placeholders"], " | ".join(find_tokens(src[rid])))
            self.assertEqual(r["source_hash"], batch.source_hash(src[rid]))
            self.assertEqual(r["batch_id"], res["batch_id"])
        self.assertEqual(by_id["demo:system:need_key"]["placeholders"], "<?key@item?>")
        self.assertIn("Blood Vial = قارورة الدم", by_id["demo:dialogue:keeper_03"]["glossary"])
        self.assertEqual(by_id["demo:ui:rest"]["context"], "زر في قائمة المصباح")

    def test_limit_and_open_batches_not_resent(self):
        first = self.export(limit=12)
        second = self.export(limit=40)
        self.assertEqual((first["rows"], second["rows"]), (12, 8))
        ids1 = {r["id"] for r in read_csv(first["file"])}
        ids2 = {r["id"] for r in read_csv(second["file"])}
        self.assertFalse(ids1 & ids2, "a string must not be in two open batches")
        self.assertIsNone(self.export()["batch_id"], "nothing left to export")

    def test_type_filter(self):
        res = self.export(types=["dialogue"])
        self.assertEqual({r["type"] for r in read_csv(res["file"])}, {"dialogue"})
        self.assertEqual(res["rows"], 5)


class ImportTests(BatchTestBase):
    def setUp(self):
        super().setUp()
        self.res = self.export(limit=20)
        self.file = Path(self.res["file"])

    def test_good_rows_need_review_error_rows_stay_draft(self):
        stats = self.do_import(self.fill(self.file))
        self.assertEqual(stats["accepted_draft"], 1)
        self.assertEqual(stats["accepted_review"], 19)
        self.assertEqual(self.status("demo:system:need_key"), "draft")           # missing <?key@item?>
        self.assertEqual(self.status("demo:dialogue:pilgrim_01"), "needs_review")  # warning only
        counts = db.status_counts(self.conn)
        self.assertEqual(counts["approved"], 0, "nothing may be approved by an import")
        self.assertEqual(counts["needs_review"], 19)
        row = db.get_string(self.conn, "demo:system:received")
        self.assertEqual((row["origin"], row["machine_model"]), ("machine", "claude"))

    def test_arabic_and_placeholders_stored_exactly(self):
        self.do_import(self.fill(self.file))
        for rid in ("demo:ui:confirm_discard", "demo:system:echoes_lost", "demo:system:received",
                    "demo:dialogue:pilgrim_02", "demo:item_desc:moth_ring"):
            self.assertEqual(db.get_string(self.conn, rid)["translation"], TRANSLATIONS[rid])
        self.assertIn("%s", db.get_string(self.conn, "demo:system:received")["translation"])

    def test_report_lists_errors_and_warnings(self):
        stats = self.do_import(self.fill(self.file))
        rep = read_csv(stats["report"])
        err = [r for r in rep if r["severity"] == "error"]
        self.assertEqual({r["id"] for r in err}, {"demo:system:need_key"})
        self.assertIn("placeholders", {r["check"] for r in err})
        warn_checks = {(r["id"], r["check"]) for r in rep if r["severity"] == "warning"}
        self.assertIn(("demo:dialogue:pilgrim_01", "punctuation"), warn_checks)
        self.assertIn(("demo:ui:rest", "duplicate_source"), warn_checks)
        stored = self.conn.execute(
            "SELECT COUNT(*) FROM validation_issues v JOIN strings s ON s.id=v.string_id"
            " WHERE s.string_key='demo:system:need_key' AND v.severity='error'").fetchone()[0]
        self.assertGreaterEqual(stored, 1)

    def test_fixed_reimport_moves_draft_to_review(self):
        self.do_import(self.fill(self.file))
        fixed = dict(TRANSLATIONS, **{"demo:system:need_key": "يتطلب <?key@item?> لفتحه."})
        stats = self.do_import(self.fill(self.file, fixed, name="fixed.csv"))
        self.assertEqual(self.status("demo:system:need_key"), "needs_review")
        self.assertEqual(stats["unchanged"], 19)
        self.assertEqual(self.conn.execute("SELECT status FROM batches").fetchone()[0], "imported")

    def test_without_bom_accepted(self):
        stats = self.do_import(self.fill(self.file, encoding="utf-8"))
        self.assertEqual(stats["rejected"], 0)

    def test_empty_translation_skipped(self):
        stats = self.do_import(self.fill(self.file, {"demo:ui:rest": "استراحة"}))
        self.assertEqual((stats["accepted_review"], stats["skipped"]), (1, 19))
        self.assertEqual(self.status("demo:ui:travel"), "pending")
        self.assertEqual(self.conn.execute("SELECT status FROM batches").fetchone()[0], "partially_imported")

    # ---- integrity / fingerprint checks
    def test_edited_source_column_rejected(self):
        def edit(rows):
            rows[0]["source"] = rows[0]["source"] + " (edited)"
        stats = self.do_import(self.fill(self.file, edit=edit))
        rep = {r["id"]: r for r in read_csv(stats["report"]) if r["result"] == "rejected"}
        self.assertEqual(list(rep), [read_csv(self.file)[0]["id"]])
        self.assertEqual(next(iter(rep.values()))["check"], "modified_source")

    def test_forged_hash_rejected(self):
        """Editing the source AND recomputing its hash must still fail (hash is checked against the batch record)."""
        def edit(rows):
            rows[0]["source"] = "Something else"
            rows[0]["source_hash"] = batch.source_hash("Something else")
        stats = self.do_import(self.fill(self.file, edit=edit))
        self.assertEqual(stats["rejected"], 1)
        self.assertEqual(self.status(read_csv(self.file)[0]["id"]), "pending")

    def test_edited_hash_column_rejected(self):
        """Only the source_hash column changed (source untouched) -> still a modified batch."""
        def edit(rows):
            rows[1]["source_hash"] = "0" * 16
        stats = self.do_import(self.fill(self.file, edit=edit))
        rej = {r["id"]: r["check"] for r in read_csv(stats["report"]) if r["result"] == "rejected"}
        self.assertEqual(rej, {read_csv(self.file)[1]["id"]: "modified_source"})

    def test_stale_batch_rejected(self):
        db.upsert_source(self.conn, "demo:ui:discard", "Throw away")
        stats = self.do_import(self.fill(self.file))
        rej = {r["id"]: r["check"] for r in read_csv(stats["report"]) if r["result"] == "rejected"}
        self.assertEqual(rej, {"demo:ui:discard": "stale"})
        self.assertEqual(self.status("demo:ui:discard"), "pending")

    def test_unknown_and_foreign_ids_rejected(self):
        db.upsert_source(self.conn, "demo:extra:not_exported", "Extra")
        def edit(rows):
            rows.append(dict(rows[0], id="demo:does_not_exist"))
            rows.append(dict(rows[0], id="demo:extra:not_exported"))
        stats = self.do_import(self.fill(self.file, edit=edit))
        rej = {r["id"]: r["check"] for r in read_csv(stats["report"]) if r["result"] == "rejected"}
        self.assertEqual(rej, {"demo:does_not_exist": "unknown_id", "demo:extra:not_exported": "not_in_batch"})

    # ---- whole-file refusals (nothing written)
    def _assert_refused(self, path, word):
        before = db.status_counts(self.conn)
        with self.assertRaises(batch.BatchError) as cm:
            self.do_import(path)
        self.assertIn(word, str(cm.exception) + " ".join(cm.exception.problems))
        self.assertEqual(db.status_counts(self.conn), before, "a refused file must not change the DB")

    def test_duplicate_id_refuses_file(self):
        self._assert_refused(self.fill(self.file, edit=lambda rows: rows.append(dict(rows[3]))), "duplicate id")

    def test_unknown_batch_refused(self):
        def edit(rows):
            for r in rows:
                r["batch_id"] = "batch_19990101_000000"
        self._assert_refused(self.fill(self.file, edit=edit), "unknown batch_id")

    def test_wrong_encoding_refused(self):
        tr = {k: v.replace("ـ", "") for k, v in TRANSLATIONS.items()}
        self._assert_refused(self.fill(self.file, tr, encoding="cp1256"), "not UTF-8")

    def test_question_marks_refused(self):
        lost = dict(TRANSLATIONS, **{"demo:ui:rest": "???????"})
        self._assert_refused(self.fill(self.file, lost), "lost Arabic encoding")

    def test_missing_column_refused(self):
        def edit(rows):
            for r in rows:
                del r["source_hash"]
        self._assert_refused(self.fill(self.file, edit=edit), "missing columns")

    # ---- overwrite protection
    def _approve(self, key):
        if self.status(key) == "draft":
            db.mark_for_review(self.conn, key, "Dana")
        db.approve(self.conn, key, "Dana")

    def test_approved_not_overwritten_without_flag(self):
        self.do_import(self.fill(self.file))
        self._approve("demo:ui:rest")
        second = self.export(statuses=("approved", "needs_review"), limit=40)
        changed = dict(TRANSLATIONS, **{"demo:ui:rest": "اِسترِح"})
        stats = self.do_import(self.fill(Path(second["file"]), changed, name="r2.csv"))
        rej = {r["id"]: r["check"] for r in read_csv(stats["report"]) if r["result"] == "rejected"}
        self.assertEqual(rej, {"demo:ui:rest": "approved_protected"})
        row = db.get_string(self.conn, "demo:ui:rest")
        self.assertEqual((row["status"], row["translation"]), ("approved", "استراحة"))

    def test_approved_overwrite_with_flag_goes_back_to_review(self):
        self.do_import(self.fill(self.file))
        self._approve("demo:ui:rest")
        second = self.export(statuses=("approved",), limit=40)
        changed = {"demo:ui:rest": "اِسترِح"}
        stats = self.do_import(self.fill(Path(second["file"]), changed, name="r3.csv"),
                               allow_overwrite_approved=True)
        row = db.get_string(self.conn, "demo:ui:rest")
        self.assertEqual((row["status"], row["translation"], row["reviewer"]), ("needs_review", "اِسترِح", None))
        self.assertIn("overwrite", {r["check"] for r in read_csv(stats["report"])})

    def test_human_draft_protected(self):
        db.set_translation(self.conn, "demo:ui:discard", "ارمِ", "human", "Dana")
        stats = self.do_import(self.fill(self.file))
        rej = {r["id"]: r["check"] for r in read_csv(stats["report"]) if r["result"] == "rejected"}
        self.assertEqual(rej, {"demo:ui:discard": "human_draft_protected"})
        self.assertEqual(db.get_string(self.conn, "demo:ui:discard")["translation"], "ارمِ")
        stats = self.do_import(self.fill(self.file, name="again.csv"), overwrite_drafts=True)
        self.assertEqual(db.get_string(self.conn, "demo:ui:discard")["translation"], TRANSLATIONS["demo:ui:discard"])

    def test_claude_cannot_approve(self):
        self.do_import(self.fill(self.file))
        with self.assertRaises(db.WorkflowError):
            db.approve(self.conn, "demo:ui:rest", "claude")

    def test_list_batches(self):
        self.do_import(self.fill(self.file))
        info = batch.list_batches(self.conn)
        self.assertEqual(len(info), 1)
        self.assertEqual(info[0]["results"], {"accepted_review": 19, "accepted_draft": 1})


class CliTests(unittest.TestCase):
    """End-to-end through cli.py, as used on Windows. All output goes to a temp folder."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self._saved = (batch.LOGS_DIR, batch.BATCH_OUT, batch.BATCH_IN)
        batch.LOGS_DIR, batch.BATCH_OUT, batch.BATCH_IN = self.d / "logs", self.d / "out", self.d / "in"

    def tearDown(self):
        batch.LOGS_DIR, batch.BATCH_OUT, batch.BATCH_IN = self._saved
        self.tmp.cleanup()

    def test_cli_round_trip(self):
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self._round_trip()

    def _round_trip(self):
        import cli
        dbp = str(self.d / "c.db")
        self.assertEqual(cli.main(["--db", dbp, "init"]), 0)
        self.assertEqual(cli.main(["--db", dbp, "glossary-load"]), 0)
        self.assertEqual(cli.main(["--db", dbp, "sources-import", str(SOURCES)]), 0)
        self.assertEqual(cli.main(["--db", dbp, "batch-export", "--limit", "20"]), 0)
        files = list((self.d / "out").glob("batch_*.csv"))
        self.assertEqual(len(files), 1)
        rows = read_csv(files[0])
        self.assertEqual(len(rows), 20)
        for r in rows:
            r["translation"] = TRANSLATIONS[r["id"]]
        ret = self.d / "ret.csv"
        with open(ret, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=batch.BATCH_COLUMNS)
            w.writeheader()
            w.writerows(rows)
        self.assertEqual(cli.main(["--db", dbp, "batch-import", str(ret)]), 0)
        self.assertEqual(cli.main(["--db", dbp, "batch-list"]), 0)
        self.assertTrue(list((self.d / "logs").glob("batch_*_report.csv")))
        self.assertTrue((self.d / "in").is_dir(), "batch-export prepares the folder for returned files")
        dup = self.d / "dup.csv"
        shutil.copy(ret, dup)
        with open(dup, "a", encoding="utf-8", newline="") as f:
            csv.DictWriter(f, fieldnames=batch.BATCH_COLUMNS).writerow(rows[0])
        self.assertEqual(cli.main(["--db", dbp, "batch-import", str(dup)]), 2, "refused file -> exit code 2")
        with db.open_db(dbp) as conn:
            counts = db.status_counts(conn)
        self.assertEqual((counts["approved"], counts["needs_review"], counts["draft"]), (0, 19, 1))


if __name__ == "__main__":
    unittest.main()
