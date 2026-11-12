"""Run the whole test suite and check that it did not touch the real project data.

Before and after the run it takes a snapshot (path, size, SHA-256) of the real folders
logs/, translation/batches/, backups/ and database/ (which holds the real localization.db).
The check runs whether the tests pass or fail.

All temporary files of the run are created in one fresh folder; after the run that folder
must be empty again (every test cleaned up after itself).

Exit codes: 0 all good, 1 tests failed, 3 real project files changed, 4 temporary files left behind.
If several problems happen, the highest code is returned.

    .venv\\Scripts\\python tools\\run_tests_isolated.py
"""
import hashlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUARDED = ("logs", "translation/batches", "backups", "database")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot(root: Path, guarded=GUARDED) -> dict:
    """{relative path: (size, sha256) | 'dir' | 'missing'} for every file and folder under the guarded folders."""
    snap = {}
    for g in guarded:
        base = Path(root) / g
        if not base.exists():
            snap[g + "/"] = "missing"
            continue
        snap[g + "/"] = "dir"
        for p in sorted(base.rglob("*")):
            if "__pycache__" in p.parts:
                continue
            rel = p.relative_to(root).as_posix()
            if p.is_file():
                snap[rel] = (p.stat().st_size, _sha256(p))
            elif p.is_dir():
                snap[rel + "/"] = "dir"
    return snap


def diff(before: dict, after: dict) -> list[str]:
    out = []
    for k in sorted(set(before) | set(after)):
        if k not in after:
            out.append(f"removed: {k}")
        elif k not in before:
            out.append(f"created: {k}")
        elif before[k] != after[k]:
            out.append(f"changed: {k}")
    return out


def main() -> int:
    sys.path.insert(0, str(ROOT))
    before = snapshot(ROOT)
    run_tmp = Path(tempfile.mkdtemp(prefix="bbal_tests_"))
    saved_tempdir = tempfile.tempdir
    tempfile.tempdir = str(run_tmp)
    try:
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    finally:
        tempfile.tempdir = saved_tempdir
    after = snapshot(ROOT)
    leftovers = sorted(p.relative_to(run_tmp).as_posix() for p in run_tmp.iterdir())
    shutil.rmtree(run_tmp, ignore_errors=True)

    code = 0 if result.wasSuccessful() else 1
    changes = diff(before, after)
    print("\n== isolation check: real logs/, translation/batches/, backups/, database/")
    if changes:
        code = max(code, 3)
        print("REAL PROJECT FILES CHANGED DURING THE TESTS:")
        for c in changes:
            print("  " + c)
    else:
        print(f"unchanged ({len(before)} entries compared)")
    print("== temporary files cleaned up by the tests")
    if leftovers:
        code = max(code, 4)
        print("LEFT BEHIND: " + ", ".join(leftovers[:20]))
    else:
        print("yes (temporary folder of the run is empty)")
    print(f"== result: {'OK' if code == 0 else 'PROBLEM'} (exit code {code})")
    return code


if __name__ == "__main__":
    sys.exit(main())
