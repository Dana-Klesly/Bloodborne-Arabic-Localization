import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests.helpers import read_csv, temp_db, SAMPLES
from database import db


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.conn = temp_db()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_import_counts(self):
        counts = db.status_counts(self.conn)
        rows = read_csv(SAMPLES)
        self.assertEqual(counts["draft"], sum(1 for r in rows if r["translation"]))
        self.assertEqual(counts["pending"], sum(1 for r in rows if not r["translation"]))
        self.assertEqual(counts["approved"], 0)

    def test_multiline_and_arabic_roundtrip(self):
        row = db.get_string(self.conn, "sample:item_desc:pilgrim_bell")
        self.assertEqual(row["source_text"].count("\n"), 1)
        self.assertIn("جرسٌ", row["translation"])

    def test_constraint_approved_needs_reviewer(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE strings SET status='approved' WHERE string_key='sample:ui:level_up'")

    def test_constraint_pending_has_no_text(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE strings SET status='pending' WHERE string_key='sample:ui:level_up'")

    def test_machine_output_is_only_draft(self):
        key = "sample:place:archive"
        status = db.set_translation(self.conn, key, "أرشيف كنيسة الشفاء", "machine", "ollama",
                                    machine_model="test-model")
        self.assertEqual(status, "draft")
        row = db.get_string(self.conn, key)
        self.assertEqual(row["origin"], "machine")
        self.assertEqual(row["machine_model"], "test-model")
        # cannot skip review
        with self.assertRaises(db.WorkflowError):
            db.approve(self.conn, key, "Dana")

    def test_full_workflow(self):
        key = "sample:place:archive"
        db.set_translation(self.conn, key, "أرشيف كنيسة الشفاء", "machine", "ollama")
        db.mark_for_review(self.conn, key, "Dana")
        with self.assertRaises(db.WorkflowError):
            db.approve(self.conn, key, "ollama")          # machine can't approve
        with self.assertRaises(db.WorkflowError):
            db.approve(self.conn, key, "  ")               # empty reviewer
        with self.assertRaises(db.WorkflowError):
            db.approve(self.conn, key, "Dana", ["missing {0}"])  # validation errors block
        db.approve(self.conn, key, "Dana")
        self.assertEqual(db.get_string(self.conn, key)["status"], "approved")
        # editing approved text -> back to draft, reviewer cleared
        db.set_translation(self.conn, key, "محفوظات كنيسة الشفاء", "human", "Dana")
        row = db.get_string(self.conn, key)
        self.assertEqual((row["status"], row["reviewer"]), ("draft", None))
        hist = self.conn.execute("SELECT COUNT(*) FROM history WHERE string_id=?", (row["id"],)).fetchone()[0]
        self.assertGreaterEqual(hist, 4)

    def test_source_change_reopens_approved(self):
        key = "sample:ui:stat_insight"
        db.mark_for_review(self.conn, key, "t")
        db.approve(self.conn, key, "Dana")
        db.upsert_source(self.conn, key, "Insight (changed)")
        self.assertEqual(db.get_string(self.conn, key)["status"], "needs_review")

    def test_empty_translation_returns_to_pending(self):
        key = "sample:ui:stat_insight"
        self.assertEqual(db.set_translation(self.conn, key, "  ", "human", "t"), "pending")

    def test_csv_never_imports_approved(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "in.csv"
            p.write_text("string_key,source_text,translation,status\nx:1,Hello,مرحبا,approved\n",
                         encoding="utf-8")
            stats = db.import_csv(self.conn, p)
        self.assertEqual(stats["approved_downgraded"], 1)
        self.assertEqual(db.get_string(self.conn, "x:1")["status"], "needs_review")

    def test_export_import_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.csv"
            n = db.export_csv(self.conn, out)
            self.assertEqual(n, len(read_csv(SAMPLES)))
            raw = out.read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM for Excel")
            stats = db.import_csv(self.conn, out)
        self.assertEqual(stats["unchanged"], n)
        self.assertEqual(stats["translations_set"], 0)


if __name__ == "__main__":
    unittest.main()
