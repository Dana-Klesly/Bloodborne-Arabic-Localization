"""Batch CSV with male/female versions (gender + translation_female columns).

Uses only our invented samples (tests/samples/batch_sources.csv).
"""
import csv
import tempfile
import unittest
from pathlib import Path

from tests.helpers import read_csv
from common.paths import GLOSSARY_CSV, SAMPLES_DIR
from database import db
from glossary.glossary import load_into_db
from translation import batch

SOURCES = SAMPLES_DIR / "batch_sources.csv"
BASE_TR = {r["id"]: r["translation"] for r in read_csv(SAMPLES_DIR / "batch_translations.csv")}
BASE_TR["demo:system:need_key"] = "يتطلب فتحه <?key@item?>."           # no errors in the base set
BASE_TR["demo:dialogue:pilgrim_01"] = "هل رأيت برج جماعة الجوقة؟ يقولون إن السماء أقرب هناك!"
BASE_TR["demo:ui:rest_dup"] = "استراحة"
REC, PIL = "demo:system:received", "demo:dialogue:pilgrim_01"
MALE, FEMALE = "تسلّمتَ %s.", "تسلّمتِ %s."
OLD_COLUMNS = ["batch_id", "id", "type", "speaker", "context", "max_length", "source",
               "placeholders", "glossary", "source_hash", "translation", "translator_note"]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.conn = db.connect(self.dir / "t.db")
        db.init_db(self.conn)
        load_into_db(self.conn, GLOSSARY_CSV)
        batch.import_sources(self.conn, SOURCES, report_dir=self.dir)
        db.set_active_gender(self.conn, "female")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def export(self, **kw):
        kw.setdefault("out_dir", self.dir / "out")
        return batch.export_batch(self.conn, **kw)

    def fill(self, exported, tr=None, female=None, columns=None, edit=None, encoding="utf-8", name="ret.csv"):
        tr = BASE_TR if tr is None else tr
        female = female or {}
        rows = read_csv(exported)
        for r in rows:
            r["translation"] = tr.get(r["id"], "")
            r["translation_female"] = female.get(r["id"], "")
        if edit:
            edit(rows)
        cols = columns or list(rows[0].keys())
        out = self.dir / name
        with open(out, "w", encoding=encoding, newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        return out

    def imp(self, path, **kw):
        return batch.import_batch(self.conn, path, report_dir=self.dir, **kw)

    def v(self, key=REC):
        return {r["gender"]: r for r in db.versions(self.conn, key)}

    def report(self, stats):
        return read_csv(stats["report"])

    def healthy(self):
        self.assertEqual(self.conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        bad = self.conn.execute(
            "SELECT m.string_key FROM strings m WHERE m.gender='male' AND NOT EXISTS (SELECT 1 FROM strings f"
            " WHERE f.string_key=m.string_key AND f.gender='female')").fetchall()
        self.assertEqual(bad, [])
        self.assertEqual(db.get_active_gender(self.conn), "female", "imports never change the active gender")
        self.assertEqual(db.status_counts(self.conn)["approved"], self.approved_expected)

    approved_expected = 0

    def split_received(self):
        first = self.export(limit=40)
        self.imp(self.fill(first["file"], female={REC: FEMALE}, tr=dict(BASE_TR, **{REC: MALE})))


class ExportTests(Base):
    def test_new_columns_and_gender_values(self):
        res = self.export(limit=40)
        rows = read_csv(res["file"])
        self.assertEqual(list(rows[0].keys()), batch.BATCH_COLUMNS)
        self.assertIn("gender", rows[0])
        self.assertIn("translation_female", rows[0])
        self.assertEqual({r["gender"] for r in rows}, {"shared"})
        self.assertEqual({r["translation_female"] for r in rows}, {""})

    def test_split_string_exported_once_as_male_female(self):
        self.split_received()
        res = self.export(statuses=("needs_review", "draft"), limit=40)
        rows = [r for r in read_csv(res["file"]) if r["id"] == REC]
        self.assertEqual(len(rows), 1, "one row per id, not per version")
        self.assertEqual(rows[0]["gender"], "male+female")


class ImportSplitTests(Base):
    def test_split_via_translation_female(self):
        res = self.export(limit=40)
        stats = self.imp(self.fill(res["file"], female={REC: FEMALE}, tr=dict(BASE_TR, **{REC: MALE})))
        v = self.v()
        self.assertEqual(set(v), {"male", "female"})
        self.assertEqual((v["male"]["translation"], v["female"]["translation"]), (MALE, FEMALE))
        self.assertEqual((v["male"]["status"], v["female"]["status"]), ("needs_review", "needs_review"))
        self.assertEqual((v["male"]["origin"], v["female"]["origin"]), ("machine", "machine"))
        self.assertEqual(v["female"]["machine_model"], batch.MACHINE_MODEL)
        self.assertEqual(set(self.v(PIL)), {"shared"}, "pilgrim stays shared")
        self.assertEqual(self.v(PIL)["shared"]["translation"], BASE_TR[PIL])
        self.assertEqual((stats["split"], stats["accepted_review"], stats["approved_by_import"]), (1, 20, 0))
        rep = self.report(stats)
        self.assertIn("gender", rep[0])
        got = {(r["id"], r["gender"], r["check"]) for r in rep}
        self.assertIn((REC, "male", "variants_diacritics_only"), got)
        self.assertIn((REC, "female", "split"), got)
        self.assertEqual(len(self.conn.execute("SELECT * FROM history WHERE string_id=?",
                                               (v["female"]["id"],)).fetchall()), 2, "created + needs_review")
        self.healthy()

    def test_empty_female_creates_nothing(self):
        stats = self.imp(self.fill(self.export(limit=40)["file"]))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM strings WHERE gender<>'shared'").fetchone()[0], 0)
        self.assertEqual(stats["split"], 0)
        self.healthy()

    def test_identical_female_stays_shared_with_warning(self):
        stats = self.imp(self.fill(self.export(limit=40)["file"], female={REC: BASE_TR[REC] + " "}))
        self.assertEqual(set(self.v()), {"shared"})
        self.assertIn((REC, "female_identical"), {(r["id"], r["check"]) for r in self.report(stats)})

    def test_female_without_male_rejected(self):
        tr = dict(BASE_TR)
        tr[REC] = ""
        stats = self.imp(self.fill(self.export(limit=40)["file"], tr=tr, female={REC: FEMALE}))
        rej = {r["id"]: r["check"] for r in self.report(stats) if r["result"] == "rejected"}
        self.assertEqual(rej, {REC: "female_without_male"})
        self.assertEqual((set(self.v()), self.v()["shared"]["status"]), ({"shared"}, "pending"))

    def test_female_placeholder_error_keeps_only_female_in_draft(self):
        stats = self.imp(self.fill(self.export(limit=40)["file"], tr=dict(BASE_TR, **{REC: MALE}),
                                   female={REC: "تسلّمتِ الأداة."}))
        v = self.v()
        self.assertEqual((v["male"]["status"], v["female"]["status"]), ("needs_review", "draft"))
        rep = self.report(stats)
        self.assertIn((REC, "female", "placeholders", "error"), {(r["id"], r["gender"], r["check"], r["severity"]) for r in rep})
        self.assertEqual(self.conn.execute("SELECT status FROM batches").fetchone()[0], "partially_imported")
        self.healthy()

    def test_lost_encoding_in_female_refuses_file(self):
        before = db.status_counts(self.conn)
        with self.assertRaises(batch.BatchError) as cm:
            self.imp(self.fill(self.export(limit=40)["file"], female={REC: "??????? %s."}))
        self.assertIn("translation_female", " ".join(cm.exception.problems))
        with self.assertRaises(batch.BatchError):
            self.imp(self.fill(self.export(include_open=True, limit=40)["file"], female={REC: "تسلّمتِ � %s."},
                               name="r2.csv"))
        self.assertEqual(db.status_counts(self.conn), before)

    def test_split_respects_approved_protection(self):
        first = self.export(limit=40)
        self.imp(self.fill(first["file"]))
        db.approve(self.conn, REC, "Dana")
        self.approved_expected = 1
        second = self.export(statuses=("approved",), limit=40)
        stats = self.imp(self.fill(second["file"], tr={REC: MALE}, female={REC: FEMALE}, name="r2.csv"))
        rej = {r["id"]: r["check"] for r in self.report(stats) if r["result"] == "rejected"}
        self.assertEqual(rej, {REC: "approved_protected"})
        self.assertEqual(set(self.v()), {"shared"})
        self.assertEqual(self.v()["shared"]["status"], "approved")
        self.healthy()
        db.reject(self.conn, REC, "Dana", "needs a female version")    # a human takes the approval back first
        self.approved_expected = 0
        third = self.export(statuses=("draft",), limit=40, include_open=True)
        self.imp(self.fill(third["file"], tr={REC: MALE}, female={REC: FEMALE}, name="r3.csv"))
        v = self.v()
        self.assertEqual((v["male"]["status"], v["male"]["reviewer"]), ("needs_review", None))
        self.approved_expected = 0
        self.healthy()

    def test_split_respects_human_draft_protection(self):
        db.set_translation(self.conn, REC, "حصلت على %s.", "human", "Dana")
        stats = self.imp(self.fill(self.export(statuses=("pending", "draft"), limit=40)["file"],
                                   tr=dict(BASE_TR, **{REC: MALE}), female={REC: FEMALE}))
        rej = {r["id"]: r["check"] for r in self.report(stats) if r["result"] == "rejected"}
        self.assertEqual(rej, {REC: "human_draft_protected"})
        self.assertEqual(set(self.v()), {"shared"})


class ImportGenderedTests(Base):
    def setUp(self):
        super().setUp()
        self.split_received()

    def next_batch(self):
        return self.export(statuses=("needs_review", "draft"), limit=40, include_open=True)

    def test_old_format_file_updates_male_only(self):
        res = self.next_batch()
        stats = self.imp(self.fill(res["file"], tr={REC: "نلتَ %s."}, columns=OLD_COLUMNS))
        v = self.v()
        self.assertEqual((v["male"]["translation"], v["female"]["translation"]), ("نلتَ %s.", FEMALE))
        self.assertIn((REC, "female", "female_unchanged"), {(r["id"], r["gender"], r["check"]) for r in self.report(stats)})
        self.healthy()

    def test_both_versions_updated(self):
        self.imp(self.fill(self.next_batch()["file"], tr={REC: "نلتَ %s."}, female={REC: "نلتِ %s."}))
        v = self.v()
        self.assertEqual((v["male"]["translation"], v["female"]["translation"]), ("نلتَ %s.", "نلتِ %s."))
        self.assertEqual({v[g]["status"] for g in v}, {"needs_review"})

    def test_approved_female_protected_male_still_updated(self):
        db.approve(self.conn, REC, "Dana", gender="female")
        self.approved_expected = 1
        stats = self.imp(self.fill(self.next_batch()["file"], tr={REC: "نلتَ %s."}, female={REC: "نلتِ %s."}))
        v = self.v()
        self.assertEqual((v["female"]["translation"], v["female"]["status"]), (FEMALE, "approved"))
        self.assertEqual(v["male"]["translation"], "نلتَ %s.")
        rej = {(r["id"], r["gender"]): r["check"] for r in self.report(stats) if r["result"] == "rejected"}
        self.assertEqual(rej, {(REC, "female"): "approved_protected"})
        self.healthy()

    def test_stale_source_rejects_both_versions(self):
        res = self.next_batch()
        db.upsert_source(self.conn, REC, "You got %s.")
        stats = self.imp(self.fill(res["file"], tr={REC: "نلتَ %s."}, female={REC: "نلتِ %s."}))
        rej = {r["id"]: r["check"] for r in self.report(stats) if r["result"] == "rejected"}
        self.assertEqual(rej, {REC: "stale"})
        self.assertEqual((self.v()["male"]["translation"], self.v()["female"]["translation"]), (MALE, FEMALE))

    def test_edited_hash_rejected(self):
        def edit(rows):
            for r in rows:
                if r["id"] == REC:
                    r["source_hash"] = "0" * 16
        stats = self.imp(self.fill(self.next_batch()["file"], tr={REC: "نلتَ %s."}, female={REC: "نلتِ %s."}, edit=edit))
        rej = {r["id"]: r["check"] for r in self.report(stats) if r["result"] == "rejected"}
        self.assertEqual(rej, {REC: "modified_source"})

    def test_cli_round_trip_with_female_column(self):
        import contextlib
        import io
        import cli
        dbp = str(self.dir / "t.db")
        self.conn.commit()
        res = self.next_batch()
        self.conn.commit()
        ret = self.fill(res["file"], tr={REC: "نلتَ %s."}, female={REC: "نلتِ %s."})
        saved = batch.LOGS_DIR
        batch.LOGS_DIR = self.dir / "logs"
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["--db", dbp, "batch-import", str(ret)]), 0)
        finally:
            batch.LOGS_DIR = saved
        self.conn.close()
        self.conn = db.connect(dbp)
        self.assertEqual(self.v()["female"]["translation"], "نلتِ %s.")
        self.healthy()


if __name__ == "__main__":
    unittest.main()
