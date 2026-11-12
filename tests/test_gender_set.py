"""Active player gender: which text the player sees (select-gender, text, export-set).

Choosing a gender is read-side only: it must never change translations, statuses or history.
"""
import contextlib
import csv
import io
import tempfile
import unittest
from pathlib import Path

from tests.helpers import ROOT  # noqa: F401  (sets sys.path)
from database import db

REC, PIL, REST = "demo:system:received", "demo:dialogue:pilgrim_01", "demo:ui:rest"
MALE, FEMALE = "تسلّمتَ %s.", "تسلّمتِ %s."
PILGRIM_AR = "هل رأيت برج جماعة الجوقة؟ يقولون إن السماء أقرب هناك!"


def snapshot(conn):
    return (sorted(tuple(r) for r in conn.execute("SELECT * FROM strings")),
            sorted(tuple(r) for r in conn.execute("SELECT * FROM history")))


class TextSetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "s.db"
        self.conn = db.connect(self.path)
        db.init_db(self.conn)
        for key, src, tr in ((REC, "You received %s.", MALE), (PIL, "Have you seen the Choir's tower?", PILGRIM_AR),
                             (REST, "Rest", "استراحة")):
            db.upsert_source(self.conn, key, src)
            db.set_translation(self.conn, key, tr, "human", "Dana")
        db.split_gender(self.conn, REC, FEMALE, "Dana")
        db.mark_for_review(self.conn, REST, "Dana")
        db.approve(self.conn, REST, "Dana")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def as_dict(self, rows):
        return {r["string_key"]: (r["gender"], r["translation"]) for r in rows}

    def test_each_gender_sees_shared_texts_and_its_own_version(self):
        male = self.as_dict(db.text_set(self.conn, "male"))
        female = self.as_dict(db.text_set(self.conn, "female"))
        self.assertEqual(male[REC], ("male", MALE))
        self.assertEqual(female[REC], ("female", FEMALE))
        for key in (PIL, REST):
            self.assertEqual(male[key], female[key])
            self.assertEqual(male[key][0], "shared")
        self.assertEqual(male[PIL][1], PILGRIM_AR)

    def test_every_id_exactly_once(self):
        keys = {r[0] for r in self.conn.execute("SELECT string_key FROM strings")}
        for g in ("male", "female"):
            rows = db.text_set(self.conn, g)
            self.assertEqual(len(rows), len(keys))
            self.assertEqual({r["string_key"] for r in rows}, keys)

    def test_active_gender_is_used_and_required(self):
        with self.assertRaises(db.WorkflowError):
            db.text_set(self.conn)
        with self.assertRaises(db.WorkflowError):
            db.text_for(self.conn, REC)
        db.set_active_gender(self.conn, "female")
        self.assertEqual(db.text_for(self.conn, REC)["translation"], FEMALE)
        self.assertEqual(db.text_for(self.conn, PIL)["translation"], PILGRIM_AR)
        self.assertEqual(db.text_for(self.conn, REC, "male")["translation"], MALE, "--gender overrides")
        db.set_active_gender(self.conn, "male")
        self.assertEqual(db.text_for(self.conn, REC)["translation"], MALE)

    def test_switching_gender_changes_nothing_else(self):
        before = snapshot(self.conn)
        for g in ("male", "female", "male", "female"):
            db.set_active_gender(self.conn, g)
            db.text_set(self.conn)
            db.text_for(self.conn, REC)
        self.assertEqual(snapshot(self.conn), before)
        self.assertEqual(db.get_string(self.conn, REST)["status"], "approved")

    def test_cli_select_text_export(self):
        import cli
        out = io.StringIO()
        before = snapshot(self.conn)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["--db", str(self.path), "export-set"]), 2, "no gender selected yet")
            for g, expected in (("male", MALE), ("female", FEMALE)):
                self.assertEqual(cli.main(["--db", str(self.path), "select-gender", g]), 0)
                f = self.dir / f"set_{g}.csv"
                self.assertEqual(cli.main(["--db", str(self.path), "export-set", "--out", str(f)]), 0)
                rows = {r["string_key"]: r for r in csv.DictReader(open(f, encoding="utf-8-sig", newline=""))}
                self.assertEqual(rows[REC]["translation"], expected)
                self.assertEqual(rows[REC]["version"], g)
                self.assertEqual(rows[PIL]["translation"], PILGRIM_AR)
                self.assertEqual(rows[PIL]["version"], "shared")
                self.assertEqual(cli.main(["--db", str(self.path), "text", REC]), 0)
        self.assertIn(f'"translation": "{FEMALE}"', out.getvalue())
        self.conn.close()
        self.conn = db.connect(self.path)
        self.assertEqual(snapshot(self.conn), before)
        self.assertEqual(db.get_active_gender(self.conn), "female")


if __name__ == "__main__":
    unittest.main()
