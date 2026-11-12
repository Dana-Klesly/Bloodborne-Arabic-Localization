"""tools/run_tests_isolated.py must notice any change in the guarded folders."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

from tests.helpers import ROOT

_spec = importlib.util.spec_from_file_location("run_tests_isolated", ROOT / "tools" / "run_tests_isolated.py")
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "logs").mkdir()
        (self.root / "logs" / "a.txt").write_text("a", encoding="utf-8")
        (self.root / "database").mkdir()
        (self.root / "database" / "x.db").write_bytes(b"db")

    def tearDown(self):
        self.tmp.cleanup()

    def snap(self):
        return runner.snapshot(self.root, ("logs", "translation/batches", "database"))

    def test_no_change_no_diff(self):
        self.assertEqual(runner.diff(self.snap(), self.snap()), [])

    def test_created_changed_removed_are_reported(self):
        before = self.snap()
        (self.root / "logs" / "new.csv").write_text("n", encoding="utf-8")
        (self.root / "database" / "x.db").write_bytes(b"dB")                 # same size, other bytes
        (self.root / "logs" / "a.txt").unlink()
        (self.root / "translation" / "batches" / "out").mkdir(parents=True)
        d = runner.diff(before, self.snap())
        self.assertIn("created: logs/new.csv", d)
        self.assertIn("changed: database/x.db", d)
        self.assertIn("removed: logs/a.txt", d)
        self.assertIn("changed: translation/batches/", d)                    # was missing, now exists
        self.assertIn("created: translation/batches/out/", d)

    def test_pycache_ignored(self):
        before = self.snap()
        (self.root / "database" / "__pycache__").mkdir()
        (self.root / "database" / "__pycache__" / "m.pyc").write_bytes(b"x")
        self.assertEqual(runner.diff(before, self.snap()), [])


if __name__ == "__main__":
    unittest.main()
