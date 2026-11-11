-- Bloodborne-Arabic-Localization : main database schema (SQLite), version 2
-- The DB stores LOGICAL Arabic text only (never shaped / presentation forms).
-- Game files are never stored here; only the text needed for translation.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- One row per translatable string VERSION.  (schema v2)
-- string_key is the stable public id of the original text ("sample:<group>:<n>",
-- later "<container>/<fmg>:<entry_id>"). Every row also has its own internal id.
--
-- gender:
--   shared  one translation used for both player genders (the normal case)
--   male    / female  two versions of the same string_key, only for texts whose
--           Arabic changes with the player character's gender
-- Rules (enforced below):
--   * one base row per string_key: either 'shared' or 'male'
--   * a 'female' row needs a 'male' row with the same string_key
--   * a 'male' row cannot be removed / turned back while its 'female' row exists
--   * a 'female' row can only be deleted during an explicit merge (meta.variant_op='merge')
CREATE TABLE IF NOT EXISTS strings (
    id              INTEGER PRIMARY KEY,
    string_key      TEXT    NOT NULL,
    gender          TEXT    NOT NULL DEFAULT 'shared'
                    CHECK (gender IN ('shared','male','female')),
    source_file     TEXT,              -- filled by the future extractor
    fmg_name        TEXT,              -- filled by the future extractor
    entry_id        INTEGER,           -- filled by the future extractor
    category        TEXT,              -- ui / item_name / item_desc / dialogue / ...
    speaker         TEXT,
    context         TEXT,              -- notes for the translator
    source_text     TEXT    NOT NULL,
    max_length      INTEGER,           -- hard limit in characters (NULL = unknown)
    max_line_length INTEGER,           -- per-line limit in characters (NULL = unknown)
    translation     TEXT,
    status          TEXT    NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','draft','needs_review','approved')),
    origin          TEXT    NOT NULL DEFAULT 'none'
                    CHECK (origin IN ('none','machine','human')),
    machine_model   TEXT,              -- e.g. model name, when origin='machine'
    translator      TEXT,
    reviewer        TEXT,
    notes           TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now')),

    -- pending  <=> no translation yet
    CHECK ((status = 'pending') = (translation IS NULL OR trim(translation) = '')),
    -- approved requires a named human reviewer
    CHECK (status <> 'approved' OR (reviewer IS NOT NULL AND trim(reviewer) <> ''))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_strings_key_gender ON strings(string_key, gender);
CREATE UNIQUE INDEX IF NOT EXISTS ux_strings_base      ON strings(string_key) WHERE gender IN ('shared','male');
CREATE INDEX IF NOT EXISTS idx_strings_status   ON strings(status);
CREATE INDEX IF NOT EXISTS idx_strings_category ON strings(category);
CREATE INDEX IF NOT EXISTS idx_strings_source   ON strings(source_text);

CREATE TRIGGER IF NOT EXISTS trg_strings_female_insert
BEFORE INSERT ON strings WHEN NEW.gender = 'female'
BEGIN
    SELECT RAISE(ABORT, 'female version needs a male version with the same string_key')
    WHERE NOT EXISTS (SELECT 1 FROM strings WHERE string_key = NEW.string_key AND gender = 'male');
END;

CREATE TRIGGER IF NOT EXISTS trg_strings_female_update
BEFORE UPDATE OF gender, string_key ON strings WHEN NEW.gender = 'female'
BEGIN
    SELECT RAISE(ABORT, 'female version needs a male version with the same string_key')
    WHERE NOT EXISTS (SELECT 1 FROM strings
                      WHERE string_key = NEW.string_key AND gender = 'male' AND id <> NEW.id);
END;

CREATE TRIGGER IF NOT EXISTS trg_strings_male_update
BEFORE UPDATE OF gender, string_key ON strings
WHEN OLD.gender = 'male' AND (NEW.gender <> 'male' OR NEW.string_key <> OLD.string_key)
BEGIN
    SELECT RAISE(ABORT, 'male version cannot change while its female version exists')
    WHERE EXISTS (SELECT 1 FROM strings WHERE string_key = OLD.string_key AND gender = 'female');
END;

CREATE TRIGGER IF NOT EXISTS trg_strings_male_delete
BEFORE DELETE ON strings WHEN OLD.gender = 'male'
BEGIN
    SELECT RAISE(ABORT, 'male version cannot be deleted while its female version exists')
    WHERE EXISTS (SELECT 1 FROM strings WHERE string_key = OLD.string_key AND gender = 'female');
END;

CREATE TRIGGER IF NOT EXISTS trg_strings_female_delete
BEFORE DELETE ON strings WHEN OLD.gender = 'female'
BEGIN
    SELECT RAISE(ABORT, 'female version can only be removed by an explicit merge')
    WHERE coalesce((SELECT value FROM meta WHERE key = 'variant_op'), '') <> 'merge';
END;

-- Every change of translation or status is logged.
CREATE TABLE IF NOT EXISTS history (
    id              INTEGER PRIMARY KEY,
    string_id       INTEGER NOT NULL REFERENCES strings(id) ON DELETE CASCADE,
    old_status      TEXT,
    new_status      TEXT,
    old_translation TEXT,
    new_translation TEXT,
    origin          TEXT,
    changed_by      TEXT,
    note            TEXT,
    changed_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS glossary (
    id              INTEGER PRIMARY KEY,
    term_en         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    aliases_en      TEXT,   -- other English spellings/forms to detect, separated by |
    approved_ar     TEXT NOT NULL,
    variants_ar     TEXT,   -- accepted Arabic synonyms / inflected forms, separated by |
    forbidden_ar    TEXT,   -- translations that must NOT be used, separated by |
    category        TEXT,   -- concept / place / faction / item / name / ui ...
    context         TEXT,   -- where / how the term is used
    notes           TEXT,
    status          TEXT NOT NULL DEFAULT 'proposed'
                    CHECK (status IN ('proposed','approved')),
    case_sensitive  INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    female_ar          TEXT,   -- (v2) form used in the female version, e.g. Hunter -> الصيادة
    female_variants_ar TEXT    -- (v2) other accepted female forms, separated by |
);

-- Translation batches sent to a translator (Claude) as CSV and imported back.
-- source_hash = fingerprint of the English source at export time; an import whose
-- source no longer matches is rejected (stale or edited batch).
CREATE TABLE IF NOT EXISTS batches (
    batch_id    TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    file_path   TEXT,
    row_count   INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'exported'
                CHECK (status IN ('exported','imported','partially_imported')),
    imported_at TEXT,
    note        TEXT
);

CREATE TABLE IF NOT EXISTS batch_items (
    batch_id    TEXT    NOT NULL REFERENCES batches(batch_id) ON DELETE CASCADE,
    string_id   INTEGER NOT NULL REFERENCES strings(id) ON DELETE CASCADE,
    string_key  TEXT    NOT NULL,
    source_hash TEXT    NOT NULL,
    result      TEXT,   -- NULL until imported; accepted_review / accepted_draft / unchanged / rejected / skipped
    PRIMARY KEY (batch_id, string_id)
);

-- Results of the last validation run (replaced on every run).
CREATE TABLE IF NOT EXISTS validation_issues (
    id         INTEGER PRIMARY KEY,
    string_id  INTEGER REFERENCES strings(id) ON DELETE CASCADE,
    severity   TEXT NOT NULL CHECK (severity IN ('error','warning','info')),
    check_name TEXT NOT NULL,
    message    TEXT NOT NULL,
    run_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
