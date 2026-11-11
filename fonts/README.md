# fonts

Put Arabic fonts with an open license (SIL OFL) here as `.ttf` / `.otf`.
Candidates to test: Amiri, Noto Naskh Arabic, Scheherazade New, Noto Kufi Arabic.

- `cli.py font-check --font <file>` shows which Arabic characters a font supports.
- `cli.py preview` uses the first font in this folder. If the folder is empty it falls back
  to Windows system fonts (Tahoma / Arial / Segoe UI). Those are fine for previews only:
  they must not be shipped inside the mod.

No font is downloaded or bundled automatically.
