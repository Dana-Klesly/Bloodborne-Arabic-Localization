# translation

Batch translation by CSV. The translator is Claude (no local model, no Ollama).
Rules for the translator: `CLAUDE_TRANSLATOR.md`.

```
batches/out/<batch_id>.csv   exported by `cli.py batch-export`   (send to the translator)
batches/in/<batch_id>.csv    returned file with the translation column filled
```

Flow:
1. `cli.py sources-import file.csv`: English strings. Columns `id, source, context, type` (+ `speaker, max_length`).
   The whole file is refused if an id is duplicated or invalid, a type is unknown or a source is empty.
2. `cli.py batch-export --limit 40`: next batch of `pending` strings. Strings already in an open batch are not sent again.
3. The translator fills `translation` (and optionally `translator_note`).
4. `cli.py batch-import batches/in/<batch_id>.csv`
   - The whole file is refused (nothing written) if it is not UTF-8, has duplicate ids, belongs to an
     unknown batch, misses columns, or Arabic was replaced by `?`.
   - A row is rejected if its id is unknown or not part of the batch, if `source` / `source_hash` were
     edited, or if the English source changed in the DB since export (stale).
   - Approved rows are protected (`--allow-overwrite-approved`). Human drafts are protected (`--overwrite-drafts`).
   - Validator errors leave the row in `draft`; otherwise `needs_review`. **Nothing is approved automatically.**
   - Report: `logs/<batch_id>_report.csv`.
5. Review: `cli.py preview --status needs_review`, then `cli.py approve <id> --reviewer <name>`.
