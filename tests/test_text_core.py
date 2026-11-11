import unittest

from tests.helpers import ROOT  # noqa: F401  (sets sys.path)
from common import arabic as ar
from common.placeholders import compare_tokens, find_tokens


class PlaceholderTests(unittest.TestCase):
    def test_families(self):
        text = "<?key@confirm?> {0} {name} %s %1$d <font color=\"#f00\">x</font> [BUTTON_A] $PLAYER$ \\n 100%%"
        self.assertEqual(find_tokens(text), [
            "<?key@confirm?>", "{0}", "{name}", "%s", "%1$d", '<font color="#f00">', "</font>",
            "[BUTTON_A]", "$PLAYER$", "\\n", "%%"])

    def test_order_free_compare(self):
        missing, extra = compare_tokens("{0} of {1}", "{1} من {0}")
        self.assertFalse(missing)
        self.assertFalse(extra)

    def test_missing_and_extra(self):
        missing, extra = compare_tokens("Got {0} and %s", "حصلت على {1}")
        self.assertEqual(set(missing), {"{0}", "%s"})
        self.assertEqual(set(extra), {"{1}"})

    def test_plain_text_has_no_tokens(self):
        self.assertEqual(find_tokens("Sell item(s)? 50% off"), [])


class ArabicTests(unittest.TestCase):
    def test_shaping_joins_letters(self):
        shaped = ar.shape_for_display("سلام")
        self.assertTrue(ar.PRESENTATION_FORMS.search(shaped))
        self.assertFalse(ar.ARABIC_LETTERS.search(shaped), "all letters should become joined forms")

    def test_rtl_visual_order(self):
        # visual order: the first logical letter (س) must end up last (rightmost)
        shaped = ar.shape_for_display("سلام")
        import unicodedata
        self.assertIn("SEEN", unicodedata.name(shaped[-1]))

    def test_lam_alef_ligature(self):
        shaped = ar.shape_for_display("لا")
        self.assertEqual(len(shaped), 1)
        self.assertTrue(0xFEF5 <= ord(shaped) <= 0xFEFC)

    def test_multiline_keeps_line_order(self):
        shaped = ar.shape_for_display("أول\nثاني")
        self.assertEqual(shaped.count("\n"), 1)
        first, second = shaped.split("\n")
        self.assertEqual(first, ar.shape_for_display("أول"))
        self.assertEqual(second, ar.shape_for_display("ثاني"))

    def test_placeholder_survives_shaping(self):
        shaped = ar.shape_for_display("حصلت على {0} من أصداء الدم")
        self.assertIn("{0}", shaped)

    def test_codes_not_reordered_by_bidi(self):
        # regression: bidi used to turn '%s' into 's%'
        for code in ("%s", "%1$d", "{0}", "{count}", "<?key@confirm?>", "[BUTTON_A]", "$PLAYER$"):
            with self.subTest(code):
                self.assertIn(code, ar.shape_for_display(f"استخدمت {code}."))
        shaped = ar.shape_for_display("أتبيع {count} مقابل {price}؟")
        self.assertLess(shaped.index("{price}"), shaped.index("{count}"), "RTL: later token sits further left")

    def test_numbers_stay_ltr(self):
        self.assertIn("125", ar.shape_for_display("لديك 125 صدى"))

    def test_normalize_for_match(self):
        self.assertEqual(ar.normalize_for_match("أَصْدَاءُ"), ar.normalize_for_match("اصداء"))
        self.assertIn("للصياد", ar.definite_forms("الصياد"))
        self.assertIn("صياد", ar.definite_forms("الصياد"))


if __name__ == "__main__":
    unittest.main()
