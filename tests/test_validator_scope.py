"""validator.run() with a status filter must not delete the stored results of rows it did not check."""
import tempfile
import unittest
from pathlib import Path

from tests.helpers import ROOT  # noqa: F401  (sets sys.path)
from database import db
from validation import validator


class PartialValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.conn = db.connect(self.dir / "v.db")
        db.init_db(self.conn)
        # row A: draft with an error (missing %s); row B: needs_review with a warning (Latin '?')
        db.upsert_source(self.conn, "k:a", "You received %s.")
        db.set_translation(self.conn, "k:a", "حصلت على شيء.", "human", "Dana")
        db.upsert_source(self.conn, "k:b", "Another one? Go away.")
        db.set_translation(self.conn, "k:b", "آخر? ارحل.", "human", "Dana")
        db.mark_for_review(self.conn, "k:b", "Dana")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def issues(self):
        return {(r["string_key"], r["check_name"]) for r in self.conn.execute(
            "SELECT s.string_key, v.check_name FROM validation_issues v JOIN strings s ON s.id = v.string_id")}

    def test_partial_run_keeps_results_of_rows_not_selected(self):
        validator.run(self.conn, report_dir=self.dir)                      # full run: results for both rows
        before = self.issues()
        self.assertIn(("k:a", "placeholders"), before)
        self.assertIn(("k:b", "punctuation"), before)

        validator.run(self.conn, statuses=["needs_review"], report_dir=self.dir)   # selects k:b only
        after = self.issues()
        self.assertIn(("k:a", "placeholders"), after, "results of the row not selected must stay")
        self.assertIn(("k:b", "punctuation"), after)
        self.assertEqual(after, before)

    def test_partial_run_replaces_results_of_selected_rows(self):
        validator.run(self.conn, report_dir=self.dir)
        db.set_translation(self.conn, "k:b", "آخر؟ ارحل.", "human", "Dana")   # fix the warning
        db.mark_for_review(self.conn, "k:b", "Dana")
        validator.run(self.conn, statuses=["needs_review"], report_dir=self.dir)
        after = self.issues()
        self.assertNotIn(("k:b", "punctuation"), after, "old result of a re-checked row is replaced")
        self.assertIn(("k:a", "placeholders"), after)

    def test_full_run_still_replaces_everything(self):
        validator.run(self.conn, report_dir=self.dir)
        self.conn.execute("INSERT INTO validation_issues(string_id, severity, check_name, message)"
                          " VALUES (NULL, 'info', 'stale', 'left from an old run')")
        validator.run(self.conn, report_dir=self.dir)
        self.assertNotIn((None, "stale"), {(r[0], r[1]) for r in self.conn.execute(
            "SELECT string_id, check_name FROM validation_issues")})


if __name__ == "__main__":
    unittest.main()
