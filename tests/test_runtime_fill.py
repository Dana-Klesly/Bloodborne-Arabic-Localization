"""Runtime placeholder fill after shaping (from translation test 3).

Risk being guarded:
    the mod ships a template that is ALREADY shaped (joined letters + RTL order),
    and the game engine later inserts a RAW Arabic value into %s.
    Result: the value's letters stay unjoined and in logical order -> the word looks
    reversed and broken in game ("حصلت على داي‍ص‍لا ف‍ي‍س").

Anti-false-positive design
  * Expected visual strings are written out as hard-coded code points (checked letter by
    letter), not computed with shape_for_display, so the test does not just compare the
    shaping function with itself.
  * The detector is proven to say "broken" on a known-broken input AND "ok" on known-good
    inputs; a detector that always answers the same way fails these tests.

Only invented test text is used. No game files, no DB, no translation files.
"""
import unicodedata
import unittest

from tests.helpers import ROOT  # noqa: F401  (sets sys.path)
from common.arabic import ARABIC_LETTERS, PRESENTATION_FORMS, shape_for_display

TEMPLATE = "حصلت على %s."          # translation of "You received %s."
AR_VALUE = "سيف الصياد"
EN_VALUE = "Hunter's Sword"

# Hard-coded expected visual output (left-to-right code point order).
#   ".%s " + ﻰﻠﻋ (على) + " " + ﺖﻠﺼﺣ (حصلت)
EXPECTED_TEMPLATE = ".%s ﻰﻠﻋ ﺖﻠﺼﺣ"
#   ﺩﺎﻴﺼﻟﺍ (الصياد) + " " + ﻒﻴﺳ (سيف)
EXPECTED_AR_VALUE = "ﺩﺎﻴﺼﻟﺍ ﻒﻴﺳ"
EXPECTED_AR_LINE = "." + EXPECTED_AR_VALUE + " ﻰﻠﻋ ﺖﻠﺼﺣ"
EXPECTED_EN_LINE = ".Hunter's Sword ﻰﻠﻋ ﺖﻠﺼﺣ"

EXPECTED_VALUE_FORMS = [  # (letter, form) for EXPECTED_AR_VALUE, verified by hand
    ("DAL", "ISOLATED"), ("ALEF", "FINAL"), ("YEH", "MEDIAL"), ("SAD", "MEDIAL"),
    ("LAM", "INITIAL"), ("ALEF", "ISOLATED"), (None, None),
    ("FEH", "FINAL"), ("YEH", "MEDIAL"), ("SEEN", "INITIAL"),
]


def fill(template: str, value: str, token: str = "%s") -> str:
    """What a simple game engine does: plain string substitution, nothing else."""
    assert template.count(token) == 1, "test template must contain the token exactly once"
    return template.replace(token, value)


def broken_runtime_fill(visual: str) -> list[str]:
    """Problems in a line that is meant to be fully shaped visual text.

    Empty list = looks consistent. Detects raw (unshaped) Arabic letters next to shaped
    ones, which is exactly what a raw value inside a pre-shaped template produces.
    """
    problems = []
    raw = ARABIC_LETTERS.findall(visual)
    if raw and PRESENTATION_FORMS.search(visual):
        problems.append(f"mixed shaped and raw Arabic: {len(raw)} raw letters ({''.join(raw)})")
    if "%s" in visual or "s%" in visual:
        problems.append("placeholder left unfilled or reversed")
    return problems


def _forms(visual: str):
    out = []
    for ch in visual:
        if ch == " ":
            out.append((None, None))
            continue
        parts = unicodedata.name(ch).split()   # e.g. ARABIC LETTER DAL ISOLATED FORM
        out.append((parts[2], parts[-2]))
    return out


class ReferenceDataTests(unittest.TestCase):
    """The hard-coded references must themselves be right, or every test below is meaningless."""

    def test_expected_value_letter_forms(self):
        self.assertEqual(_forms(EXPECTED_AR_VALUE), EXPECTED_VALUE_FORMS)

    def test_reference_strings_are_fully_shaped(self):
        for s in (EXPECTED_TEMPLATE, EXPECTED_AR_LINE, EXPECTED_EN_LINE):
            self.assertIsNone(ARABIC_LETTERS.search(s), s)


class TemplateTests(unittest.TestCase):
    def test_placeholder_kept_exactly(self):
        visual = shape_for_display(TEMPLATE)
        self.assertEqual(visual, EXPECTED_TEMPLATE)
        self.assertEqual(visual.count("%s"), 1)
        self.assertNotIn("s%", visual)


class DetectorSanityTests(unittest.TestCase):
    """Prove the detector can say both 'broken' and 'ok' (no fake pass)."""

    def test_detector_flags_known_broken_line(self):
        known_broken = EXPECTED_TEMPLATE.replace("%s", AR_VALUE)
        self.assertTrue(broken_runtime_fill(known_broken))

    def test_detector_accepts_known_good_lines(self):
        self.assertEqual(broken_runtime_fill(EXPECTED_AR_LINE), [])
        self.assertEqual(broken_runtime_fill(EXPECTED_EN_LINE), [])

    def test_detector_flags_unfilled_or_reversed_placeholder(self):
        self.assertTrue(broken_runtime_fill(EXPECTED_TEMPLATE))
        self.assertTrue(broken_runtime_fill(EXPECTED_TEMPLATE.replace("%s", "s%")))


class RuntimeFillTests(unittest.TestCase):
    def setUp(self):
        self.shaped_template = shape_for_display(TEMPLATE)

    # --- case 1: raw Arabic value into a pre-shaped template -> must be detected
    def test_raw_arabic_value_is_detected(self):
        line = fill(self.shaped_template, AR_VALUE)
        problems = broken_runtime_fill(line)
        self.assertTrue(problems, "raw Arabic value inside a shaped template must be flagged")
        self.assertNotEqual(line, EXPECTED_AR_LINE)
        # the concrete symptom: the value is still in logical order and unjoined
        self.assertIn(AR_VALUE, line)
        self.assertNotIn(EXPECTED_AR_VALUE, line)

    # --- case 2: value shaped the same way first -> must pass
    def test_preshaped_arabic_value_passes(self):
        value = shape_for_display(AR_VALUE)
        self.assertEqual(value, EXPECTED_AR_VALUE)
        line = fill(self.shaped_template, value)
        self.assertEqual(broken_runtime_fill(line), [])
        self.assertEqual(line, EXPECTED_AR_LINE)
        self.assertEqual(_forms(line[1:1 + len(EXPECTED_AR_VALUE)]), EXPECTED_VALUE_FORMS)

    # --- reference pipeline: fill logical text first, shape last -> must give the same line
    def test_fill_then_shape_matches(self):
        self.assertEqual(shape_for_display(fill(TEMPLATE, AR_VALUE)), EXPECTED_AR_LINE)

    # --- English value
    def test_english_value_raw(self):
        line = fill(self.shaped_template, EN_VALUE)
        self.assertEqual(broken_runtime_fill(line), [])
        self.assertEqual(line, EXPECTED_EN_LINE)
        self.assertIn("Hunter's Sword", line)          # not reversed, apostrophe kept

    def test_english_value_fill_then_shape(self):
        self.assertEqual(shape_for_display(fill(TEMPLATE, EN_VALUE)), EXPECTED_EN_LINE)

    def test_logical_text_untouched(self):
        """Filling the stored (logical) template must not alter template or value."""
        logical = fill(TEMPLATE, AR_VALUE)
        self.assertEqual(logical, "حصلت على سيف الصياد.")
        self.assertIsNone(PRESENTATION_FORMS.search(logical))


if __name__ == "__main__":
    unittest.main()
