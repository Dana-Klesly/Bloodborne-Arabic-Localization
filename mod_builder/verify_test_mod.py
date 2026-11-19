"""Re-read a built test mod and check it against the original game files and the draft CSV.

    python -m mod_builder.verify_test_mod --mod mod_builder/output/BloodborneArabic_test

Checks (exit code 0 only if all pass):
- for every text folder built (engus, enggb): the archives open; every FMG keeps the base's ids
  (base = bbport's layer file, or the game's); every string is the base's text unless a CSV row
  (draft = shaped translation, preserved = literal), an extras row (bbport's added texts) or the
  Quit to Desktop texts ask otherwise; deferred rows and strings bbport changed keep the base;
  codes/placeholders and line counts survive;
- the font: the 7652 game glyphs are byte-identical, codes sorted and unique, the other tags
  unchanged, and every character the texts use has a glyph.
--preview writes PNGs drawn ONLY from the mod's font.gfx glyph outlines with a plain
left-to-right pen (no shaping, no bidi), roughly what Scaleform does.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from common.arabic import shape_for_display  # noqa: E402
from common.placeholders import find_tokens  # noqa: E402
from mod_builder import fromsoft, swf_font  # noqa: E402
from mod_builder.build_test_mod import (ARCHIVES, LOCALE, QUIT_TEXTS, archive_name,  # noqa: E402
                                         default_draft, fill_sources, load_draft, load_extras)


def fmgs(path: Path):
    bnd = fromsoft.unpack_dcx(path.read_bytes())
    return {fid: fromsoft.read_fmg(d)[1] for fid, d in fromsoft.bnd4_contents(bnd).items()}


def check_locale(loc, info, game: Path, mod: Path, rows, extras, strip_harakat: bool, fail):
    """One text folder: every string is either the base's (bbport layer or game file), or what
    the CSV row / extras row / Quit to Desktop text asks for. Nothing else may differ."""
    shape = lambda t: shape_for_display(t, delete_harakat=strip_harakat)  # noqa: E731
    counts = Counter()
    ref = {a: fmgs(game / "msg" / LOCALE / f"{a}.msgbnd.dcx") for a in ARCHIVES}
    used = set()
    new_all = {}
    for arc in ARCHIVES:
        base_path = Path(info[arc]["base"])
        base = fmgs(base_path)
        gl = fmgs(game / "msg" / loc / f"{arc}.msgbnd.dcx")
        new = fmgs(mod / "dvdroot_ps4" / "msg" / loc / f"{arc}.msgbnd.dcx")
        new_all[arc] = new
        no_layer = base_path == game / "msg" / loc / f"{arc}.msgbnd.dcx"
        if set(base) != set(new):
            fail(f"{loc} {arc}: FMG file ids differ")
            continue
        expected = {}
        for r in rows:
            if archive_name(r["source_file"]) != arc:
                continue
            fid, sid = int(r["bnd_entry_id"]), int(r["entry_id"])
            if ref[arc][fid].get(sid) != r["source"] or sid not in base[fid]:
                continue
            if sid in gl[fid] and base[fid][sid] != gl[fid][sid]:
                counts["kept_bbport_text"] += 1
                continue
            st, tr = r["status"], r["translation"]
            if st == "draft" and tr.strip():
                expected[(fid, sid)] = shape(tr)
                if tr.count("\n") != expected[(fid, sid)].count("\n"):
                    fail(f"{loc} {r['id']}: line count changed")
            elif st == "preserved":
                expected[(fid, sid)] = tr or r["source"]
            counts[f"{st}"] += 1
        if no_layer and arc == "menu":
            for (fid, sid), text in QUIT_TEXTS.items():
                expected[(fid, sid)] = shape(text)
        for r in extras:
            if r["archive"] == arc and base.get(int(r["fmg"]), {}).get(int(r["entry_id"])) == r["source"]:
                expected[(int(r["fmg"]), int(r["entry_id"]))] = shape(r["translation"])
                counts["bbport_extra"] += 1
        allowed_new = set(info[arc].get("ids_added", []))
        for fid in base:
            extra_ids = set(new[fid]) - set(base[fid])
            if set(base[fid]) - set(new[fid]) or (extra_ids and fid not in allowed_new):
                fail(f"{loc} {arc} FMG {fid}: ids differ from the base")
            for sid, got in new[fid].items():
                want = expected.get((fid, sid), base[fid].get(sid))
                if got != want:
                    fail(f"{loc} {arc} FMG {fid} id {sid}: unexpected text")
                    continue
                if (fid, sid) not in expected:
                    counts["unchanged_from_base"] += 1
                src = base[fid].get(sid)
                if src is not None and got is not None and Counter(find_tokens(got)) != Counter(find_tokens(src)):
                    fail(f"{loc} {arc} FMG {fid} id {sid}: codes differ from the base text")
                if got:
                    used |= {ord(c) for c in got if c not in "\n\r"}
    return counts, used, new_all


def check_font(game: Path, mod: Path, used: set, fail):
    g = (game / "menu" / LOCALE / "font.gfx").read_bytes()
    m = (mod / "dvdroot_ps4" / "menu" / LOCALE / "font.gfx").read_bytes()
    import struct
    if struct.unpack_from("<I", m, 4)[0] != len(m):
        fail("font.gfx length field wrong")
    gt = [(c, g[p:p + n]) for c, _, p, n in swf_font.swf_tags(g)]
    mt = [(c, m[p:p + n]) for c, _, p, n in swf_font.swf_tags(m)]
    if [c for c, _ in gt] != [c for c, _ in mt]:
        fail("font.gfx tag list differs")
    for (c1, b1), (c2, b2) in zip(gt, mt):
        if c1 != 75 and b1 != b2:
            fail(f"font.gfx tag {c1} changed")
    gf = swf_font.parse_font3([b for c, b in gt if c == 75][0])
    mf = swf_font.parse_font3([b for c, b in mt if c == 75][0])
    if mf.codes != sorted(set(mf.codes)):
        fail("font codes not sorted/unique")
    idx = {c: i for i, c in enumerate(mf.codes)}
    for i, c in enumerate(gf.codes):
        j = idx.get(c)
        if j is None or mf.shapes[j] != gf.shapes[i] or mf.advances[j] != gf.advances[i] \
                or mf.bounds[j] != gf.bounds[i]:
            fail(f"game glyph U+{c:04X} changed")
            break
    if (mf.ascent, mf.descent, mf.leading, mf.kerning) != (gf.ascent, gf.descent, gf.leading, gf.kerning):
        fail("font metrics/kerning changed")
    for s in mf.shapes:
        swf_font.decode_shape(s)
    missing = sorted(c for c in used if c not in idx)
    if missing:
        fail("characters without a glyph: " + " ".join(f"U+{c:04X}" for c in missing[:30]))
    return gf, mf, missing


def _glyph_polys(shape, x, base, k):
    polys = []
    for contour in swf_font.decode_shape(shape):
        pts, cx, cy = [], None, None
        for seg in contour:
            if seg[0] in ("M", "L"):
                cx, cy = seg[1], seg[2]
                pts.append((cx, cy))
            else:
                for t in (.2, .4, .6, .8, 1):
                    pts.append(((1 - t) ** 2 * cx + 2 * (1 - t) * t * seg[1] + t * t * seg[3],
                                (1 - t) ** 2 * cy + 2 * (1 - t) * t * seg[2] + t * t * seg[4]))
                cx, cy = seg[3], seg[4]
        if len(pts) > 2:
            polys.append([(x + u * k, base + v * k) for u, v in pts])
    return polys


def render(font, lines, path, px=34, width=1500):
    """Draw text with the DefineFont3 outlines only: glyph after glyph, left to right, each
    glyph filled even-odd. No shaping or bidi happens here."""
    from PIL import Image, ImageChops, ImageDraw
    k = px / 20480
    idx = {c: i for i, c in enumerate(font.codes)}
    line_h = int(px * 1.6)
    h = 20 + sum(line_h * (t.count("\n") + 1) + 16 for _, t in lines)
    ink = Image.new("L", (width, h), 0)
    labels = Image.new("RGB", (width, h), (18, 16, 20))
    dl = ImageDraw.Draw(labels)
    y = 10
    for label, text in lines:
        dl.text((8, y + 8), label[:34], fill=(150, 140, 130))
        for line in text.split("\n"):
            x, base = 250.0, y + px * 1.1
            for ch in line:
                i = idx.get(ord(ch))
                if i is None:
                    dl.rectangle([x, base - px * .7, x + px * .4, base], outline=(220, 60, 60))
                    x += px * .5
                    continue
                g = Image.new("L", (width, h), 0)
                for poly in _glyph_polys(font.shapes[i], x, base, k):
                    m = Image.new("L", (width, h), 0)
                    ImageDraw.Draw(m).polygon(poly, fill=255)
                    g = ImageChops.logical_xor(g.convert("1"), m.convert("1")).convert("L")
                ink = ImageChops.lighter(ink, g)
                x += font.advances[i] * k
            y += line_h
        y += 16
    out = Image.composite(Image.new("RGB", (width, h), (236, 228, 212)), labels, ink)
    out.save(path)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mod", type=Path, required=True)
    p.add_argument("--game", type=Path, default=ROOT / "game" / "CUSA03173" / "dvdroot_ps4")
    p.add_argument("--draft", type=Path, default=None)
    p.add_argument("--preview", type=Path, help="write preview PNGs into this folder")
    a = p.parse_args(argv)
    problems = []

    def fail(msg):
        problems.append(msg)

    report = json.loads((a.mod / "build_report.json").read_text(encoding="utf-8"))
    rows = load_draft(a.draft or default_draft())
    fill_sources(rows, {arc: fmgs(a.game / "msg" / LOCALE / f"{arc}.msgbnd.dcx") for arc in ARCHIVES})
    extras = load_extras(Path(report["extras_csv"])) if report.get("extras_csv") else []
    used, all_counts, news = set(), {}, {}
    for loc, info in report["locales"].items():
        c, u, news[loc] = check_locale(loc, info, a.game, a.mod, rows, extras,
                                       report.get("strip_harakat", False), fail)
        all_counts[loc] = dict(c)
        used |= u
    counts, untouched, new = all_counts, None, news[LOCALE] if LOCALE in news else next(iter(news.values()))
    gf, mf, missing = check_font(a.game, a.mod, used, fail)
    summary = {"by_locale": counts,
               "distinct_chars_in_texts": len(used), "font_glyphs": [len(gf.codes), len(mf.codes)],
               "problems": len(problems)}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for msg in problems[:50]:
        print("PROBLEM:", msg)
    if a.preview:
        a.preview.mkdir(parents=True, exist_ok=True)
        samples = [r for r in rows if r["status"] == "draft"]
        pick = []
        for want in ("item_name", "item_desc", "dialogue", "ui", "system", "place"):
            pick += [r for r in samples if r["type"] == want][:3]
        lines = [(r["id"].split(":", 2)[2], new[archive_name(r["source_file"])][int(r["bnd_entry_id"])][int(r["entry_id"])])
                 for r in pick]
        for r in extras[:200]:
            if r["source"] in ("Quit to Desktop", "PC Graphics", "Rebirth", "Hunt Bell",
                               "Restart the game now\nto apply the changes?"):
                lines.append((f"bbport {r['archive']}:{r['fmg']}:{r['entry_id']}",
                              new[r["archive"]][int(r["fmg"])][int(r["entry_id"])]))
        render(mf, lines, a.preview / "preview_from_mod_font.png")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
