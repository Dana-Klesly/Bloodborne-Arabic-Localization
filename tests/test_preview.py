import tempfile
import unittest
from pathlib import Path

from tests.helpers import SAMPLES, read_csv
from fonts.preview import PreviewItem, find_font, render
from fonts.coverage import coverage

try:
    FONT = find_font()
except FileNotFoundError:  # pragma: no cover
    FONT = None


@unittest.skipIf(FONT is None, "no Arabic-capable font on this machine")
class PreviewTests(unittest.TestCase):
    def test_render_samples(self):
        items = [PreviewItem(r["string_key"], r["source_text"], r["translation"])
                 for r in read_csv(SAMPLES) if r["translation"]]
        with tempfile.TemporaryDirectory() as d:
            res = render(items, Path(d) / "p.png", font_path=FONT)
            self.assertTrue(Path(res["path"]).stat().st_size > 10_000)
        self.assertEqual(len(res["items"]), len(items))
        self.assertFalse(any(i["missing_glyphs"] for i in res["items"]), res["items"])

    def test_overflow_detected(self):
        with tempfile.TemporaryDirectory() as d:
            res = render([PreviewItem("narrow", "Level Up", "رفع المستوى", max_px=40)],
                         Path(d) / "o.png", font_path=FONT)
        self.assertTrue(res["items"][0]["overflow"])

    def test_wrap_fits(self):
        with tempfile.TemporaryDirectory() as d:
            res = render([PreviewItem("wrap", "", "قارورة دم تُركت طويلاً في البرد ولم يعد أحد يذكر صاحبها",
                                      max_px=260)], Path(d) / "w.png", font_path=FONT, wrap=True)
        self.assertFalse(res["items"][0]["overflow"])

    def test_font_coverage_report(self):
        rep = coverage(FONT)
        self.assertEqual(rep["groups"]["arabic_letters"]["missing"], [])


if __name__ == "__main__":
    unittest.main()
