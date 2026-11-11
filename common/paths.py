"""Project paths. Everything is relative to the project root, so the project can be moved."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DATABASE_DIR = ROOT / "database"
DEFAULT_DB = DATABASE_DIR / "localization.db"
SCHEMA_FILE = DATABASE_DIR / "schema.sql"

GLOSSARY_DIR = ROOT / "glossary"
GLOSSARY_CSV = GLOSSARY_DIR / "glossary.csv"

TRANSLATION_DIR = ROOT / "translation"
FONTS_DIR = ROOT / "fonts"
LOGS_DIR = ROOT / "logs"
TESTS_DIR = ROOT / "tests"
SAMPLES_DIR = TESTS_DIR / "samples"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
