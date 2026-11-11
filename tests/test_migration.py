"""Schema v1 -> v2 migration (male/female versions) and the new v2 constraints.

The v1 database is built from tests/samples/schema_v1.sql, an exact copy of the
schema.sql that shipped before the migration.
"""
import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import ROOT  # noqa: F401  (sets sys.path)
from common.paths import SAMPLES_DIR
from database import db

V1_SCHEMA = (SAMPLES_DIR / "schema_v1.sql").read_text(encoding="utf-8")


def make_v1(path: Path) -> None:
    c = sqlite3.connect(path)
    c.executescript(V1_SCHEMA)
    c.executescript("""
        INSERT INTO meta(key, value) VALUES ('schema_version', '1'), ('active_gender', 'female');
        INSERT INTO strings(id, string_key, category, context, source_text, max_length, translation, status,
                            origin, translator, reviewer, notes, created_at, updated_at) VALUES
          (7,  'demo:ui:rest',         'ui',       'زر',  'Rest',               16,   'استراحة', 'approved',
                'human', 'Dana', 'Dana', NULL, '2026-11-01 10:00:00', '2026-11-02 10:00:00'),
          (8,  'demo:system:received', 'system',   NULL,  'You received %s.',   NULL, 'حصلت على %s.', 'needs_review',
                'machine', NULL, NULL, 'n', '2026-11-01 10:00:00', '2026-11-03 10:00:00'),
          (12, 'demo:dialogue:k1',     'dialogue', 'عجوز', 'You came back.',    NULL, NULL, 'pending',
                'none', NULL, NULL, NULL, '2026-11-01 10:00:00', '2026-11-01 10:00:00');
        INSERT INTO history(string_id, old_status, new_status, old_translation, new_translation, origin, changed_by)
          VALUES (7, 'pending', 'draft', NULL, 'استراحة', 'human', 'Dana'),
                 (7, 'needs_review', 'approved', 'استراحة', 'استراحة', 'human', 'Dana'),
                 (8, 'pending', 'draft', NULL, 'حصلت على %s.', 'machine', 'claude');
        INSERT INTO glossary(term_en, approved_ar, variants_ar, status) VALUES ('Hunter', 'الصياد', 'صياد', 'proposed');
        INSERT INTO batches(batch_id, row_count) VALUES ('batch_1', 2);
        INSERT INTO batch_items(batch_id, string_id, string_key, source_hash)
          VALUES ('batch_1', 8, 'demo:system:received', 'aaaa'), ('batch_1', 12, 'demo:dialogue:k1', 'bbbb');
        INSERT INTO validation_issues(string_id, severity, check_name, message) VALUES (8, 'warning', 'x', 'y');
    """)
    c.commit()
    c.close()


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.path = self.d / "localization.db"
        self.backups = self.d / "backups"
        make_v1(self.path)
        raw = sqlite3.connect(self.path)
        self.v1_strings = sorted(raw.execute("SELECT * FROM strings").fetchall())
        self.v1_history = sorted(raw.execute("SELECT * FROM history").fetchall())
        self.v1_items = sorted(raw.execute("SELECT * FROM batch_items").fetchall())
        raw.close()
        self.conn = db.connect(self.path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def migrate(self):
        return db.migrate(self.conn, self.backups)

    # --- before
    def test_v1_is_refused_without_migration(self):
        with self.assertRaises(db.MigrationRequired):
            db.init_db(self.conn)
        with self.assertRaises(db.MigrationRequired):
            with db.open_db(self.path):
                pass
        self.assertEqual(db.schema_version(self.conn), 1, "refusing must not change anything")

    # --- the migration itself
    def test_versions_and_backup(self):
        rep = self.migrate()
        self.assertEqual((rep["version_before"], rep["version_after"], rep["migrated"]), (1, 2, True))
        self.assertEqual((rep["integrity"], rep["foreign_key_problems"]), ("ok", []))
        b = Path(rep["backup"]["path"])
        self.assertTrue(b.is_file())
        self.assertEqual(b.parent, self.backups)
        bc = sqlite3.connect(b)
        self.assertEqual(bc.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], "1")
        self.assertEqual(sorted(bc.execute("SELECT * FROM strings").fetchall()), self.v1_strings)
        self.assertEqual(bc.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        bc.close()

    def test_ids_values_history_and_links_preserved(self):
        self.migrate()
        cols = ",".join(db._V1_STRING_COLUMNS)
        self.assertEqual(sorted(tuple(r) for r in self.conn.execute(f"SELECT {cols} FROM strings")),
                         [tuple(r) for r in self.v1_strings])
        self.assertEqual({r[0] for r in self.conn.execute("SELECT gender FROM strings")}, {"shared"})
        self.assertEqual(sorted(tuple(r) for r in self.conn.execute("SELECT * FROM history")), self.v1_history)
        self.assertEqual(sorted(tuple(r) for r in self.conn.execute("SELECT * FROM batch_items")), self.v1_items)
        joined = self.conn.execute(
            "SELECT s.string_key, COUNT(h.id) FROM strings s JOIN history h ON h.string_id = s.id"
            " GROUP BY s.id ORDER BY s.id").fetchall()
        self.assertEqual([tuple(r) for r in joined], [("demo:ui:rest", 2), ("demo:system:received", 1)])
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_existing_workflow_still_works_after_migration(self):
        self.migrate()
        db.init_db(self.conn)                      # opens fine now
        row = db.get_string(self.conn, "demo:ui:rest")
        self.assertEqual((row["status"], row["reviewer"]), ("approved", "Dana"))
        db.set_translation(self.conn, "demo:dialogue:k1", "عدت.", "human", "Dana")
        self.assertEqual(db.get_string(self.conn, "demo:dialogue:k1")["status"], "draft")

    def test_glossary_and_meta(self):
        self.migrate()
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(glossary)")]
        self.assertIn("female_ar", cols)
        self.assertIn("female_variants_ar", cols)
        g = self.conn.execute("SELECT approved_ar, variants_ar, female_ar FROM glossary").fetchone()
        self.assertEqual(tuple(g), ("الصياد", "صياد", None))
        meta = dict(self.conn.execute("SELECT key, value FROM meta").fetchall())
        self.assertEqual(meta, {"schema_version": "2", "active_gender": "female"},
                         "the migration must not touch the active gender")

    def test_schema_objects_created(self):
        self.migrate()
        names = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master")}
        for n in ("ux_strings_key_gender", "ux_strings_base", "idx_strings_status", "idx_strings_category",
                  "idx_strings_source", "trg_strings_female_insert", "trg_strings_female_update",
                  "trg_strings_male_update", "trg_strings_male_delete", "trg_strings_female_delete"):
            self.assertIn(n, names)
        self.assertFalse([n for n in names if "v1_old" in n])
        sql = self.conn.execute("SELECT sql FROM sqlite_master WHERE name='history'").fetchone()[0]
        self.assertIn("REFERENCES strings(id)", sql, "history must still point to strings")

    def test_second_run_is_a_noop(self):
        self.migrate()
        rep = self.migrate()
        self.assertEqual((rep["migrated"], rep["version_after"]), (False, 2))
        self.assertEqual(len(list(self.backups.glob("*.db"))), 1, "no extra backup on a no-op")

    def test_failure_rolls_back_everything(self):
        with mock.patch.object(db, "_verify_v2", side_effect=RuntimeError("simulated")):
            with self.assertRaises(RuntimeError):
                self.migrate()
        self.assertEqual(db.schema_version(self.conn), 1)
        self.assertEqual(sorted(tuple(r) for r in self.conn.execute("SELECT * FROM strings")),
                         [tuple(r) for r in self.v1_strings])
        names = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master")}
        self.assertNotIn("strings_v1_old", names)
        self.assertNotIn("ux_strings_base", names)
        self.assertEqual(len(list(self.backups.glob("*.db"))), 1, "the backup stays")
        self.assertEqual(self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_bad_backup_stops_the_migration(self):
        """If the backup does not match the original, nothing may be migrated."""
        real = db._snapshot
        calls = {"n": 0}

        def fake(conn):
            calls["n"] += 1
            snap = real(conn)
            if calls["n"] == 1:                 # first call = the backup copy
                snap["rows"]["strings"] = snap["rows"]["strings"][:-1]
            return snap
        with mock.patch.object(db, "_snapshot", side_effect=fake):
            with self.assertRaises(RuntimeError) as cm:
                self.migrate()
        self.assertIn("backup verification failed", str(cm.exception))
        self.assertEqual(db.schema_version(self.conn), 1)

    def test_unhealthy_database_is_not_migrated(self):
        self.conn.execute("PRAGMA foreign_keys = OFF")
        self.conn.execute("INSERT INTO history(string_id, new_status) VALUES (999, 'x')")   # dangling link
        self.conn.commit()
        self.conn.execute("PRAGMA foreign_keys = ON")
        with self.assertRaises(RuntimeError):
            self.migrate()
        self.assertEqual(db.schema_version(self.conn), 1)
        self.assertFalse(self.backups.exists() and list(self.backups.glob("*.db")))

    def test_cli(self):
        import cli
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["--db", str(self.path), "status"]), 3, "v1 -> clear error, exit 3")
            self.assertEqual(cli.main(["--db", str(self.path), "migrate", "--backup-dir", str(self.backups)]), 0)
            self.assertEqual(cli.main(["--db", str(self.path), "status"]), 0)
        self.assertIn('"version_after": 2', out.getvalue())


class V2ConstraintTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "v2.db")
        db.init_db(self.conn)
        self.conn.execute("INSERT INTO strings(string_key, source_text) VALUES ('k:shared', 'Rest')")
        self.conn.execute("INSERT INTO strings(string_key, gender, source_text) VALUES ('k:g', 'male', 'You came')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def insert(self, key, gender):
        self.conn.execute("INSERT INTO strings(string_key, gender, source_text) VALUES (?,?,'x')", (key, gender))

    def test_new_database_is_v2(self):
        self.assertEqual(db.schema_version(self.conn), 2)

    def test_only_male_and_female(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert("k:n", "neutral")

    def test_female_needs_male(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert("k:shared", "female")          # shared base, no male
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert("k:none", "female")            # no base at all
        self.insert("k:g", "female")                   # ok
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert("k:g", "female")               # only one female

    def test_one_base_row_per_key(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert("k:shared", "male")
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert("k:g", "shared")

    def test_versions_share_the_id_but_have_own_internal_ids(self):
        self.insert("k:g", "female")
        rows = self.conn.execute("SELECT id, gender FROM strings WHERE string_key='k:g' ORDER BY gender").fetchall()
        self.assertEqual([r["gender"] for r in rows], ["female", "male"])
        self.assertNotEqual(rows[0]["id"], rows[1]["id"])

    def test_male_protected_while_female_exists(self):
        self.insert("k:g", "female")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE strings SET gender='shared' WHERE string_key='k:g' AND gender='male'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE strings SET string_key='k:other' WHERE string_key='k:g' AND gender='male'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM strings WHERE string_key='k:g' AND gender='male'")

    def test_female_cannot_be_moved_or_deleted_outside_merge(self):
        self.insert("k:g", "female")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE strings SET string_key='k:shared' WHERE gender='female'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM strings WHERE gender='female'")
        # the explicit merge path (implemented in a later phase) is allowed
        self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('variant_op', 'merge')")
        self.conn.execute("DELETE FROM strings WHERE gender='female'")
        self.conn.execute("DELETE FROM meta WHERE key='variant_op'")
        self.conn.execute("UPDATE strings SET gender='shared' WHERE string_key='k:g'")

    def test_old_rules_still_enforced(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE strings SET status='approved' WHERE string_key='k:shared'")


if __name__ == "__main__":
    unittest.main()
