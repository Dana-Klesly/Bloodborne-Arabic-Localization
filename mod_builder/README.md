# mod_builder

Builds a **test** bbport mod from the Arabic draft CSV: the game's English message archives with
the Arabic text, and the game font with Arabic glyphs added. Nothing is approved by it; statuses
in the CSV stay as they are.

## Why it works this way

- Bloodborne's UI is Scaleform. It draws one glyph per character, with **no Arabic shaping and no
  bidi**. So the text is shaped once at build time (presentation forms + visual order, codes such
  as `<?...?>` and `%d` protected) with `common.arabic.shape_for_display`. The CSV keeps logical
  Unicode order.
- The game font (`menu/<lang>/font.gfx`, DefineFont3 "TT-Reimin Y10 B") has no Arabic glyphs.
  `swf_font.py` adds them from a TrueType font (Amiri Bold, SIL OFL 1.1). Harakat get zero advance.
  The 7652 original glyphs stay byte-identical.
- bbport writes its own text layers below the mods (Quit to Desktop, PC Settings, Rebirth,
  Seamless co-op bells). With `--bbport-out` the build starts from those files, so they are kept;
  `translation/bbport_extras_draft.csv` translates them (draft).
- `engus` and `enggb` get the same files (same ids, same font); which one the game reads depends
  on the game version / region.

## Files

| file | what |
|---|---|
| `fromsoft.py` | DCX (zlib) / BND4 / FMG v2 read and write |
| `swf_font.py` | GFX tags, DefineFont3 parse/build, SWF shape records, TrueType -> SWF glyphs |
| `build_test_mod.py` | the build (CSV rows -> FMG strings, font, report, issues) |
| `verify_test_mod.py` | re-reads a built mod and checks every string and the font; optional PNG preview |

## Use

Needs `fonttools`, `arabic-reshaper`, `python-bidi` in the venv, the game files under `game/`
(not in git), and an Arabic TrueType font in `fonts/` (not in git; `fonts/OFL-Amiri.txt` is its
licence). Start the game once through bbport first so `out/` holds its text layers.

```
.venv\Scripts\python -m mod_builder.build_test_mod --font fonts\Amiri-Bold-arabic.ttf ^
    --font-license fonts\OFL-Amiri.txt --bbport-out E:\PS4\bbport-windows\out --name BloodborneArabic_v3
.venv\Scripts\python -m mod_builder.verify_test_mod --mod mod_builder\output\BloodborneArabic_v3
```

Output goes to `mod_builder/output/<name>/` (git-ignored). Copy that folder into
`<bbport-windows>\mods\` and enable it on "Mods & patches"; Text language = English.

Row rules: `draft` -> shaped translation; `preserved` -> written literally; `deferred_*` -> the
English text stays; a string a bbport layer changed keeps bbport's text (or the extras CSV's
Arabic for it).
