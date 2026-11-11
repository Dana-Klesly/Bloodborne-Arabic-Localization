import unittest

from tests.helpers import BAD_CASES, read_csv, temp_db, int_or_none
from common.paths import GLOSSARY_CSV
from glossary.glossary import read_csv as read_glossary, terms_in
from validation import validator
from validation.checks import check_empty, check_string


class GlossaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.terms = read_glossary(GLOSSARY_CSV)
        cls.by = {t.term_en: t for t in cls.terms}

    def test_glossary_file_is_valid(self):
        self.assertGreater(len(self.terms), 20)
        for t in self.terms:
            self.assertTrue(t.approved_ar)
            self.assertIn(t.status, ("proposed", "approved"))

    def test_longer_term_wins(self):
        names = {t.term_en for t in terms_in("Return to the Hunter's Dream", self.terms)}
        self.assertIn("Hunter's Dream", names)
        self.assertNotIn("Hunter", names)

    def test_short_term_still_found_elsewhere(self):
        names = {t.term_en for t in terms_in("A hunter returns to the Hunter's Dream", self.terms)}
        self.assertIn("Hunter", names)

    def test_word_boundaries(self):
        names = {t.term_en for t in terms_in("The Hunt begins", self.terms)}
        self.assertIn("Hunt", names)
        self.assertNotIn("Hunter", names)

    def test_arabic_clitics_accepted(self):
        hunter = self.by["Hunter"]
        for text in ("الصياد", "للصياد", "صيادٌ آخر", "والصيادون"):
            self.assertTrue(hunter.used_in(text), text)
        self.assertEqual(hunter.forbidden_used("القناص ينتظر"), ["القناص"])

    def test_case_sensitive_term(self):
        names = {t.term_en for t in terms_in("hp is low; HP restored", self.terms)}
        self.assertIn("HP", names)
        self.assertFalse(self.by["HP"].occurs_in("hp"))


class ValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.terms = read_glossary(GLOSSARY_CSV)

    def test_bad_cases_are_detected(self):
        for case in read_csv(BAD_CASES):
            with self.subTest(case["string_key"]):
                issues = check_string(case["source_text"], case["translation"], "draft",
                                      int_or_none(case["max_length"]), int_or_none(case["max_line_length"]),
                                      self.terms)
                hits = [i for i in issues if i.check == case["expected_check"]]
                self.assertTrue(hits, f"expected {case['expected_check']}, got {issues}")
                self.assertIn(case["expected_severity"], {i.severity for i in hits})

    def test_empty_translation_with_status(self):
        issues = check_empty("Hello", "", "draft")
        self.assertTrue(any(i.check == "empty" and i.severity == "error" for i in issues))

    def test_samples_have_no_errors(self):
        tmp, conn = temp_db()
        try:
            summary = validator.run(conn, report_dir=__import__("pathlib").Path(tmp.name))
            errors = conn.execute(
                "SELECT s.string_key, v.message FROM validation_issues v JOIN strings s ON s.id=v.string_id"
                " WHERE v.severity='error'").fetchall()
            self.assertEqual([tuple(e) for e in errors], [])
            dup = conn.execute("SELECT COUNT(*) FROM validation_issues WHERE check_name='duplicate_source'"
                               ).fetchone()[0]
            self.assertEqual(dup, 2, "the two different 'Level Up' translations must be flagged")
            self.assertGreater(summary["strings"], 0)
        finally:
            conn.close()
            tmp.cleanup()

    def test_font_coverage_check(self):
        from validation.checks import check_font_coverage
        latin_only = set(range(0x20, 0x7F))
        issues = check_font_coverage("مرحبا", latin_only)
        self.assertTrue(any(i.severity == "error" for i in issues))


if __name__ == "__main__":
    unittest.main()
