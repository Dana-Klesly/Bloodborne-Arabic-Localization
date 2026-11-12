# translation

Batch translation by CSV. The translator is an AI model working from the CSV (no local model, no Ollama).
Rules for the translator: `TRANSLATOR.md`.

```
batches/out/<batch_id>.csv   exported by `cli.py batch-export`   (send to the translator)
batches/in/<batch_id>.csv    returned file with the translation column filled
```

Flow:
1. `cli.py sources-import file.csv [--dry-run]`: English strings. Columns `id, source` (+ `context, type, speaker,
   max_length, max_line_length, source_file, fmg_name, entry_id`).
   The whole file is refused if an id is duplicated or invalid, a type is unknown, a number column is not a
   number or a source is empty. `--dry-run` checks and writes the report, the database is not changed.
2. `cli.py batch-export --limit 20`: next batch (default 40; the first trial used 20). Strings already in an
   open batch are not sent again. `--status draft` sends drafts again: their current translation and their
   current errors/warnings are in the read-only columns `current_translation`, `current_translation_female`
   and `issues`.
3. The translator fills `translation` (and `translation_female` / `translator_note` when needed).
4. `cli.py batch-import batches/in/<batch_id>.csv [--dry-run]`
   - The whole file is refused (nothing written) if it is not UTF-8, has duplicate ids, belongs to an
     unknown or cancelled batch, misses columns, or Arabic was replaced by `?`.
   - A row is rejected if its id is unknown or not part of the batch, if `source` / `source_hash` were
     edited, if the English source changed in the DB since export (stale) or if the row was cancelled.
   - **An approved version is never overwritten by an import** (there is no option for it). To change one,
     a reviewer runs `cli.py reject <id> --by <name>` first, then the string can be sent again.
   - Human drafts are protected (`--overwrite-drafts` replaces them).
   - Approval-related columns in the file (`status`, `reviewer`, `approved`, `approved_by`, `approved_at`,
     `approval_id`, `signature`, `origin`) are never read; the report lists them as ignored.
   - The information columns (`current_translation`, `current_translation_female`, `issues`) are never imported.
   - Validator errors leave the row in `draft`; otherwise `needs_review`. **Nothing is approved automatically.**
   - Report: `logs/<batch_id>_report.csv` (`..._report_dryrun.csv` for a dry run).
5. A batch that will not come back: `cli.py batch-cancel <batch_id> --by <name> [--reason ...]`. Its open rows
   can be exported again; nothing is deleted, translations and statuses are not touched, and the file of a
   cancelled batch is refused.
6. Review: `cli.py review-export` writes `logs/review_<time>.csv` (read-only sheet: English, Arabic, the other
   gender version, codes, glossary, errors, warnings). `cli.py preview --status needs_review` for the PNG
   check. Approving stays one string at a time: `cli.py approve <id> --reviewer <name>`.
