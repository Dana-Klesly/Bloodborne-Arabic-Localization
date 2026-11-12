"""Male / female versions: database layer, workflow, CLI.

Examples from the project:
  "You received %s."  -> male "تسلّمتَ %s."  / female "تسلّمتِ %s."
  "Have you seen the Choir's tower? ..." -> stays shared
"""
import contextlib
import csv
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests.helpers import ROOT, read_csv  # noqa: F401  (ROOT sets sys.path)
from database import db

RECEIVED = "demo:system:received"
PILGRIM = "demo:dialogue:pilgrim_01"
MALE = "تسلّمتَ %s."
FEMALE = "تسلّمتِ %s."
PILGRIM_AR = "هل رأيت برج جماعة الجوقة؟ يقولون إن السماء أقرب هناك!"


class GenderBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "g.db"
        self.conn = db.connect(self.path)
        db.init_db(self.conn)
        db.upsert_source(self.conn, RECEIVED, "You received %s.", category="system")
        db.upsert_source(self.conn, PILGRIM, "Have you seen the Choir's tower? They say the sky is closer there!",
                         category="dialogue")
        db.set_translation(self.conn, RECEIVED, "حصلت على %s.", "human", "Dana")
        db.set_translation(self.conn, PILGRIM, PILGRIM_AR, "human", "Dana")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def split(self, male=MALE, female=FEMALE, **kw):
        return db.split_gender(self.conn, RECEIVED, female, "Dana", male_text=male, **kw)

    def rows(self, key=RECEIVED):
        return {r["gender"]: r for r in db.versions(self.conn, key)}

    def history(self, string_id):
        return self.conn.execute("SELECT * FROM history WHERE string_id=? ORDER BY id", (string_id,)).fetchall()

    def healthy(self):
        self.assertEqual(self.conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        orphans = self.conn.execute(
            "SELECT string_key FROM strings f WHERE gender='female' AND NOT EXISTS"
            " (SELECT 1 FROM strings m WHERE m.string_key=f.string_key AND m.gender='male')").fetchall()
        lonely = self.conn.execute(
            "SELECT string_key FROM strings m WHERE gender='male' AND NOT EXISTS"
            " (SELECT 1 FROM strings f WHERE f.string_key=m.string_key AND f.gender='female')").fetchall()
        self.assertEqual((orphans, lonely), ([], []))


class SplitTests(GenderBase):
    def test_split_creates_pair_keeps_id_and_history(self):
        base_id = db.get_string(self.conn, RECEIVED)["id"]
        hist_before = len(self.history(base_id))
        self.split()
        r = self.rows()
        self.assertEqual(set(r), {"male", "female"})
        self.assertEqual(r["male"]["id"], base_id, "the shared row becomes the male row")
        self.assertNotEqual(r["female"]["id"], base_id)
        self.assertEqual((r["male"]["translation"], r["female"]["translation"]), (MALE, FEMALE))
        self.assertEqual((r["male"]["status"], r["female"]["status"]), ("draft", "draft"))
        self.assertEqual(r["female"]["source_text"], r["male"]["source_text"])
        self.assertEqual(r["female"]["category"], "system")
        self.assertGreater(len(self.history(base_id)), hist_before)
        self.assertEqual(len(self.history(r["female"]["id"])), 1)
        self.healthy()

    def test_split_keeps_existing_translation_as_male(self):
        db.split_gender(self.conn, RECEIVED, FEMALE, "Dana")
        self.assertEqual(self.rows()["male"]["translation"], "حصلت على %s.")

    def test_split_approved_goes_back_to_review(self):
        db.mark_for_review(self.conn, RECEIVED, "Dana")
        db.approve(self.conn, RECEIVED, "Dana")
        db.split_gender(self.conn, RECEIVED, FEMALE, "Dana")
        r = self.rows()
        self.assertEqual((r["male"]["status"], r["male"]["reviewer"]), ("needs_review", None))
        self.assertEqual(r["female"]["status"], "draft")

    def test_split_refusals(self):
        with self.assertRaises(db.WorkflowError):
            db.split_gender(self.conn, RECEIVED, "  ", "Dana")             # empty female
        db.upsert_source(self.conn, "k:new", "New")
        with self.assertRaises(db.WorkflowError):
            db.split_gender(self.conn, "k:new", "أنثى", "Dana")            # nothing to keep as male
        self.split()
        with self.assertRaises(db.WorkflowError):
            self.split()                                                   # already split
        self.assertEqual(set(self.rows()), {"male", "female"})
        self.assertEqual(set(self.rows(PILGRIM)), {"shared"}, "other strings untouched")

    def test_diacritics_only_difference_is_a_valid_split(self):
        self.split()
        r = self.rows()
        self.assertNotEqual(r["male"]["translation"], r["female"]["translation"])


class MergeTests(GenderBase):
    def test_merge_keeps_chosen_text_and_all_history(self):
        self.split()
        male_id, female_id = self.rows()["male"]["id"], self.rows()["female"]["id"]
        total_before = self.conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
        db.merge_gender(self.conn, RECEIVED, "female", "Dana")
        r = self.rows()
        self.assertEqual(set(r), {"shared"})
        self.assertEqual(r["shared"]["id"], male_id)
        self.assertEqual((r["shared"]["translation"], r["shared"]["status"]), (FEMALE, "needs_review"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM history WHERE string_id=?", (female_id,)).fetchone()[0], 0)
        total_after = self.conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
        self.assertEqual(total_after, total_before + 2, "no history row lost: +1 removal record, +1 merge")
        notes = " ".join(h["note"] or "" for h in self.history(male_id))
        self.assertIn("moved from female version", notes)
        removed = [h for h in self.history(male_id) if (h["note"] or "").startswith("female version removed")]
        self.assertEqual(removed[0]["old_translation"], FEMALE, "female text kept in history")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM meta WHERE key='variant_op'").fetchone()[0], 0)
        self.healthy()

    def test_merge_refusals(self):
        with self.assertRaises(KeyError):
            db.merge_gender(self.conn, RECEIVED, "male", "Dana")          # not split
        self.split()
        with self.assertRaises(db.WorkflowError):
            db.merge_gender(self.conn, RECEIVED, "neutral", "Dana")
        self.assertEqual(set(self.rows()), {"male", "female"})

    def test_female_cannot_be_deleted_outside_merge(self):
        self.split()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM strings WHERE gender='female'")
        with self.assertRaises(db.WorkflowError):
            db.set_translation(self.conn, RECEIVED, "", "human", "Dana", gender="female")
        self.healthy()


class WorkflowTests(GenderBase):
    def setUp(self):
        super().setUp()
        self.split()

    def test_gender_required_for_split_strings(self):
        for call in (lambda: db.mark_for_review(self.conn, RECEIVED, "Dana"),
                     lambda: db.approve(self.conn, RECEIVED, "Dana"),
                     lambda: db.reject(self.conn, RECEIVED, "Dana"),
                     lambda: db.set_translation(self.conn, RECEIVED, "x", "human", "Dana")):
            with self.assertRaises(db.WorkflowError) as cm:
                call()
            self.assertIn("--gender", str(cm.exception))

    def test_gender_refused_for_shared_strings(self):
        with self.assertRaises(db.WorkflowError):
            db.mark_for_review(self.conn, PILGRIM, "Dana", gender="male")
        with self.assertRaises(db.WorkflowError):
            db.mark_for_review(self.conn, PILGRIM, "Dana", gender="neutral")

    def test_versions_are_approved_independently(self):
        db.mark_for_review(self.conn, RECEIVED, "Dana", gender="male")
        db.approve(self.conn, RECEIVED, "Dana", gender="male")
        r = self.rows()
        self.assertEqual((r["male"]["status"], r["female"]["status"]), ("approved", "draft"))
        db.mark_for_review(self.conn, RECEIVED, "Dana", gender="female")
        db.approve(self.conn, RECEIVED, "Dana", gender="female")
        db.reject(self.conn, RECEIVED, "Dana", gender="male")
        r = self.rows()
        self.assertEqual((r["male"]["status"], r["female"]["status"]), ("draft", "approved"))

    def test_machine_cannot_approve_any_version(self):
        db.mark_for_review(self.conn, RECEIVED, "x", gender="female")
        for name in ("ai", "AI", "ollama", "machine", " "):
            with self.assertRaises(db.WorkflowError):
                db.approve(self.conn, RECEIVED, name, gender="female")
        self.assertEqual(self.rows()["female"]["status"], "needs_review")

    def test_source_change_reopens_both_approved_versions(self):
        for g in ("male", "female"):
            db.mark_for_review(self.conn, RECEIVED, "Dana", gender=g)
            db.approve(self.conn, RECEIVED, "Dana", gender=g)
        db.upsert_source(self.conn, RECEIVED, "You got %s.")
        r = self.rows()
        self.assertEqual({g: r[g]["status"] for g in r}, {"male": "needs_review", "female": "needs_review"})
        self.assertEqual({r[g]["source_text"] for g in r}, {"You got %s."})
        self.assertEqual({r[g]["reviewer"] for g in r}, {None})

    def test_source_change_keeps_drafts_as_drafts(self):
        db.upsert_source(self.conn, RECEIVED, "You got %s.")
        self.assertEqual({r["status"] for r in self.rows().values()}, {"draft"})

    def test_edit_one_version_only(self):
        db.set_translation(self.conn, RECEIVED, "نلتِ %s.", "human", "Dana", gender="female")
        r = self.rows()
        self.assertEqual((r["male"]["translation"], r["female"]["translation"]), (MALE, "نلتِ %s."))


class ActiveGenderTests(GenderBase):
    def test_default_not_set_and_set(self):
        self.assertIsNone(db.get_active_gender(self.conn))
        self.assertEqual(db.status_counts(self.conn)["active_gender"], "not set")
        db.set_active_gender(self.conn, "female")
        self.assertEqual(db.get_active_gender(self.conn), "female")
        for bad in ("neutral", "", "Male"):
            with self.assertRaises(db.WorkflowError):
                db.set_active_gender(self.conn, bad)
        self.assertEqual(db.get_active_gender(self.conn), "female")

    def test_not_changed_by_init_split_merge_or_csv(self):
        db.set_active_gender(self.conn, "male")
        self.conn.commit()
        db.init_db(self.conn)
        self.split()
        db.merge_gender(self.conn, RECEIVED, "male", "Dana")
        out = self.dir / "e.csv"
        db.export_csv(self.conn, out)
        db.import_csv(self.conn, out)
        self.assertEqual(db.get_active_gender(self.conn), "male")


class CsvTests(GenderBase):
    def test_export_has_gender_and_both_versions(self):
        self.split()
        out = self.dir / "e.csv"
        n = db.export_csv(self.conn, out)
        rows = read_csv(out)
        self.assertEqual(n, 3)
        got = {(r["string_key"], r["gender"]): r["translation"] for r in rows}
        self.assertEqual(got[(RECEIVED, "male")], MALE)
        self.assertEqual(got[(RECEIVED, "female")], FEMALE)
        self.assertEqual(got[(PILGRIM, "shared")], PILGRIM_AR)

    def test_import_targets_versions_and_never_splits(self):
        self.split()
        p = self.dir / "i.csv"
        with open(p, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["string_key", "source_text", "translation", "gender"])
            w.writerow([RECEIVED, "You received %s.", "نلتِ %s.", "female"])
            w.writerow([PILGRIM, "Have you seen the Choir's tower? They say the sky is closer there!", "x", "female"])
            w.writerow([RECEIVED, "You received %s.", "y", "shared"])
        stats = db.import_csv(self.conn, p)
        self.assertEqual(stats["gender_mismatch"], 2)
        self.assertEqual(self.rows()["female"]["translation"], "نلتِ %s.")
        self.assertEqual(self.rows()["male"]["translation"], MALE)
        self.assertEqual(set(self.rows(PILGRIM)), {"shared"})

    def test_old_csv_without_gender_column_updates_base(self):
        p = self.dir / "old.csv"
        with open(p, "w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerows([["string_key", "source_text", "translation"],
                                     [PILGRIM, "Have you seen the Choir's tower? They say the sky is closer there!",
                                      "نص آخر"]])
        db.import_csv(self.conn, p)
        self.assertEqual(db.get_string(self.conn, PILGRIM)["translation"], "نص آخر")


class CliTests(GenderBase):
    def run_cli(self, *args):
        import cli
        out, err = io.StringIO(), io.StringIO()
        self.conn.commit()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(["--db", str(self.path), *args])
            except SystemExit as exc:          # argparse rejects invalid choices
                code = exc.code
        return code, out.getvalue() + err.getvalue()

    def test_split_review_approve_merge_via_cli(self):
        code, _ = self.run_cli("gender-split", RECEIVED, "--female", FEMALE, "--male", MALE, "--by", "Dana")
        self.assertEqual(code, 0)
        code, msg = self.run_cli("review", RECEIVED, "--by", "Dana")
        self.assertEqual(code, 2)
        self.assertIn("--gender", msg)
        self.assertEqual(self.run_cli("review", RECEIVED, "--by", "Dana", "--gender", "male")[0], 0)
        self.assertEqual(self.run_cli("approve", RECEIVED, "--reviewer", "Dana", "--gender", "male")[0], 0)
        self.assertEqual(self.run_cli("approve", RECEIVED, "--reviewer", "ai", "--gender", "female")[0], 2)
        code, shown = self.run_cli("show", RECEIVED)
        self.assertEqual(code, 0)
        self.assertIn(MALE, shown)
        self.assertIn(FEMALE, shown)
        self.assertEqual(self.run_cli("gender-merge", RECEIVED, "--keep", "male", "--by", "Dana")[0], 0)

    def test_select_gender_and_status(self):
        code, out = self.run_cli("select-gender")
        self.assertIn("not set", out)
        self.assertEqual(self.run_cli("select-gender", "female")[0], 0)
        code, out = self.run_cli("status")
        self.assertIn('"active_gender": "female"', out)
        self.assertNotEqual(self.run_cli("select-gender", "neutral")[0], 0)


class ValidationTests(GenderBase):
    """Validator: every version checked on its own, plus checks between the two versions."""

    def run_validator(self):
        from validation import validator
        validator.run(self.conn, report_dir=self.dir)
        return {(r["string_key"], r["gender"], r["check_name"]): (r["severity"], r["message"]) for r in self.conn.execute(
            "SELECT s.string_key, s.gender, v.check_name, v.severity, v.message FROM validation_issues v"
            " JOIN strings s ON s.id = v.string_id")}

    def test_received_example_is_clean_and_diacritics_allowed(self):
        self.split()
        issues = self.run_validator()
        errors = {k: v for k, v in issues.items() if v[0] == "error"}
        self.assertEqual(errors, {})
        self.assertEqual(issues[(RECEIVED, "male", "variants_diacritics_only")][0], "info")
        self.assertNotIn((RECEIVED, "male", "variants_identical"), issues)
        self.assertEqual(set(self.rows()), {"male", "female"}, "diacritics-only versions are never merged")

    def test_pilgrim_stays_shared_without_issues(self):
        issues = self.run_validator()
        self.assertEqual([k for k in issues if k[0] == PILGRIM and issues[k][0] != "info"], [])
        self.assertEqual(set(self.rows(PILGRIM)), {"shared"})

    def test_identical_versions_warned(self):
        self.split(male=MALE, female=MALE + " ")       # differs only by invisible whitespace
        issues = self.run_validator()
        self.assertEqual(issues[(RECEIVED, "female", "variants_identical")][0], "warning")

    def test_placeholder_errors_per_version_and_between_versions(self):
        self.split(female="تسلّمتِ الأداة.")            # %s missing in the female version only
        issues = self.run_validator()
        self.assertEqual(issues[(RECEIVED, "female", "placeholders")][0], "error")
        self.assertNotIn((RECEIVED, "male", "placeholders"), issues)
        self.assertEqual(issues[(RECEIVED, "male", "variant_placeholders")][0], "error")

    def test_length_checked_per_version(self):
        self.conn.execute("UPDATE strings SET max_length=12 WHERE string_key=?", (RECEIVED,))
        self.split(female="تسلّمتِ بكل امتنان %s.")
        issues = self.run_validator()
        self.assertIn((RECEIVED, "female", "length"), issues)
        self.assertNotIn((RECEIVED, "male", "length"), issues)

    def test_missing_female_is_an_error_and_blocks_approval(self):
        from validation.validator import errors_for
        self.conn.execute("INSERT INTO strings(string_key, gender, source_text, translation, status)"
                          " VALUES ('k:lonely', 'male', 'You came back.', 'عدتَ.', 'needs_review')")
        issues = self.run_validator()
        self.assertEqual(issues[("k:lonely", "male", "variant_missing")][0], "error")
        errs = errors_for(self.conn, "k:lonely", gender="male")
        self.assertTrue(any("without a female" in e for e in errs))
        with self.assertRaises(db.WorkflowError):
            db.approve(self.conn, "k:lonely", "Dana", errs, gender="male")

    def test_pair_is_not_a_duplicate_source(self):
        self.split()
        issues = self.run_validator()
        self.assertFalse([k for k in issues if k[2] == "duplicate_source"])

    def test_female_glossary_form_used_by_the_validator(self):
        self.conn.execute("INSERT INTO glossary(term_en, approved_ar, variants_ar, status, female_ar, female_variants_ar)"
                          " VALUES ('Good hunter', 'الصياد الطيب', 'أيها الصياد الطيب', 'proposed',"
                          " 'الصيادة الطيبة', 'أيتها الصيادة الطيبة')")
        db.upsert_source(self.conn, "k:welcome", "Welcome, good hunter.")
        db.set_translation(self.conn, "k:welcome", "مرحباً أيها الصياد الطيب.", "human", "Dana")
        db.split_gender(self.conn, "k:welcome", "مرحباً أيتها الصيادة الطيبة.", "Dana")
        issues = self.run_validator()
        self.assertNotIn(("k:welcome", "male", "glossary"), issues)
        self.assertNotIn(("k:welcome", "female", "glossary"), issues)
        db.set_translation(self.conn, "k:welcome", "مرحباً أيها الصياد الطيب!", "human", "Dana", gender="female")
        issues = self.run_validator()
        self.assertIn(("k:welcome", "female", "glossary"), issues, "male form in the female version")

    def test_report_has_gender_column(self):
        from validation import validator
        self.split()
        rep = validator.run(self.conn, report_dir=self.dir)["report"]
        rows = read_csv(rep)
        self.assertIn("gender", rows[0])
        self.assertIn("female", {r["gender"] for r in rows})


class GlossaryFemaleTests(unittest.TestCase):
    def setUp(self):
        from glossary.glossary import Term
        self.good = Term("Good hunter", "الصياد الطيب", variants_ar=["أيها الصياد الطيب"],
                         female_ar="الصيادة الطيبة", female_variants_ar=["أيتها الصيادة الطيبة"])
        self.plain = Term("Hunter", "الصياد", variants_ar=["صياد"])

    def test_female_form_used_for_female_versions(self):
        from validation.checks import check_glossary
        src = "Welcome, good hunter."
        self.assertEqual(check_glossary(src, "مرحباً أيتها الصيادة الطيبة.", [self.good], "female"), [])
        self.assertTrue(check_glossary(src, "مرحباً أيها الصياد الطيب.", [self.good], "female"))
        self.assertEqual(check_glossary(src, "مرحباً أيها الصياد الطيب.", [self.good], "male"), [])
        self.assertTrue(check_glossary(src, "مرحباً أيتها الصيادة الطيبة.", [self.good], "male"))
        msg = check_glossary(src, "مرحباً", [self.good], "female")[0].message
        self.assertIn("الصيادة الطيبة", msg)

    def test_without_female_form_the_normal_rule_applies(self):
        from validation.checks import check_glossary
        self.assertEqual(check_glossary("A hunter.", "صيادة.", [self.plain], "female"), [])

    def test_csv_with_and_without_female_columns_and_db_roundtrip(self):
        from common.paths import GLOSSARY_CSV
        from glossary.glossary import from_db, load_into_db, read_csv
        self.assertTrue(read_csv(GLOSSARY_CSV), "the project glossary.csv loads")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "g.csv"
            with open(p, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["term_en", "approved_ar", "variants_ar", "status", "female_ar", "female_variants_ar"])
                w.writerow(["Good hunter", "الصياد الطيب", "", "proposed", "الصيادة الطيبة", "أيتها الصيادة الطيبة"])
            t = read_csv(p)[0]
            self.assertEqual((t.female_ar, t.female_variants_ar), ("الصيادة الطيبة", ["أيتها الصيادة الطيبة"]))
            conn = db.connect(Path(d) / "x.db")
            db.init_db(conn)
            load_into_db(conn, p)
            back = from_db(conn)[0]
            conn.close()
        self.assertEqual((back.female_ar, back.female_variants_ar), ("الصيادة الطيبة", ["أيتها الصيادة الطيبة"]))


if __name__ == "__main__":
    unittest.main()
