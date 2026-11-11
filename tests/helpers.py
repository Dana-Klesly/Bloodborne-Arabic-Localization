import csv
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.paths import GLOSSARY_CSV, SAMPLES_DIR  # noqa: E402
from database import db  # noqa: E402
from glossary.glossary import load_into_db  # noqa: E402

SAMPLES = SAMPLES_DIR / "samples_original.csv"
BAD_CASES = SAMPLES_DIR / "bad_cases.csv"


def temp_db():
    """Fresh DB with schema, glossary and our original samples."""
    tmp = tempfile.TemporaryDirectory()
    conn = db.connect(Path(tmp.name) / "test.db")
    db.init_db(conn)
    load_into_db(conn, GLOSSARY_CSV)
    db.import_csv(conn, SAMPLES, by="test")
    conn.commit()
    return tmp, conn


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def int_or_none(v):
    return int(v) if v and v.strip() else None
