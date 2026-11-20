"""Build a TEST bbport mod with the Arabic draft: text archives + a font with Arabic glyphs.

    python -m mod_builder.build_test_mod --font fonts/Amiri-Bold-arabic.ttf

Reads (never writes) the original game files and the draft CSV; writes only under --out:

    <out>/<name>/dvdroot_ps4/msg/engus/item.msgbnd.dcx
    <out>/<name>/dvdroot_ps4/msg/engus/menu.msgbnd.dcx
    <out>/<name>/dvdroot_ps4/menu/engus/font.gfx
    <out>/<name>/README_AR.txt, build_report.json, issues_by_id.csv

Row rules (draft CSV, one row per FMG string):
- status draft        -> translation, shaped ONCE here (presentation forms + visual order,
                         codes protected); the CSV keeps logical Unicode order.
- status preserved    -> translation written literally (no shaping).
- status deferred_*   -> the original English string is kept.
- strings not in the CSV (empty/None in the game) are left as they are.
Statuses are never changed and nothing is approved: this is a draft build for in-game tests.

bbport writes a "Quit to Desktop" text layer BELOW the mods (scripts/quit_menu.py) that edits
menu FMGs 200/201/204 ids 401307/110005. A mod's menu.msgbnd.dcx replaces that layer, so the
same ids get Arabic Quit to Desktop texts here (QUIT_TEXTS, marked as a draft in the report).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from common.arabic import shape_for_display  # noqa: E402
from common.placeholders import find_tokens  # noqa: E402
from mod_builder import fromsoft, swf_font  # noqa: E402

LOCALE = "engus"
ARCHIVES = ("item", "menu")
DRAFT_STATUSES = {"draft"}
LITERAL_STATUSES = {"preserved"}
KEEP_SOURCE_PREFIX = "deferred"

# bbport quit_menu.py ids: FMG 200 item names, 201 one-line help, 204 dialogs.
QUIT_TITLE_ID = 401307
QUIT_SYSTEM_ID = 110005
QUIT_TEXTS = {  # draft wording written for this test build; not from the translation draft
    (200, QUIT_TITLE_ID): "الخروج إلى سطح المكتب",
    (200, QUIT_SYSTEM_ID): "الخروج إلى سطح المكتب",
    (201, QUIT_TITLE_ID): "إغلاق اللعبة.",
    (201, QUIT_SYSTEM_ID): "حفظ اللعبة وإغلاقها.",
    (204, QUIT_SYSTEM_ID): "هل تريد إنهاء اللعبة والعودة إلى سطح المكتب؟",
}

# The big centre-screen banners (YOU DIED, BLOOD ECHOES RETRIEVED, PREY SLAUGHTERED, NIGHTMARE
# SLAIN...) are drawn by menu/fe.gfx at size 124 in bold; plain Arabic letters look poor there.
# --banner-style drawn (default when the banner font exists): each banner becomes ONE Private-Use
# character whose glyph is the whole phrase, shaped by HarfBuzz in a display font (optionally with
# blood drips) - like a picture, but the game still colours, fades and centres it.
# english: the game's English; arabic: the draft text in the normal Arabic glyphs.
BANNER_IDS = {("menu", 200, sid) for sid in range(522000, 522008)}
BANNER_FIRST_CODE = 0xE000
BANNER_BOX = (-18020, 2460)   # the game font's ascent..descent
BANNER_LETTERS = 15700        # the game font's capital height ("YOU DIED"): size 1.0 = as tall
BANNER_CENTRE = -15700 / 2 + 2500  # letters centred on the capitals' middle, nudged down (in-game: was too high)


def load_banner_texts(path: Path) -> dict[int, str]:
    """{string id: Arabic phrase} from translation/banners_ar.csv (id,text)."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {int(r["id"].rsplit(":", 1)[1]): r["text"].strip() for r in csv.DictReader(f) if r["text"].strip()}


def banner_glyphs(texts: dict[int, str], font_path: Path, drips: bool, size: float = 1.3, offset: float = 0):
    """-> ({string id: PUA string}, {code: (shape bytes, advance)}, info). A glyph advance is a
    signed 16-bit number, so a wide phrase is cut into slices of at most 30000 units, one
    Private-Use character each, drawn side by side. The same phrase reuses its characters."""
    import math
    import pathops
    from mod_builder import blood_drips, phrase_glyph
    from mod_builder.blood_drips import _from_path, _to_path
    codes, glyphs, info = {}, {}, {}
    by_text = {}
    next_code = BANNER_FIRST_CODE
    for sid in sorted(texts):
        text = texts[sid]
        if text not in by_text:
            h0 = 20480
            cs, adv = phrase_glyph.phrase_contours(font_path, text, height=h0)
            # size and place by the LETTERS (drips only hang below them)
            ly = [v for c in cs for seg in c for v in seg[2::2]]
            top, bottom = min(ly), max(ly)
            if drips:
                cs = blood_drips.add_drips(cs, h0, seed=2)
            k = BANNER_LETTERS * size / (bottom - top)
            mid_box, mid = BANNER_CENTRE + offset, (top + bottom) / 2

            def tf(seg):
                vals = list(seg[1:])
                for i in range(0, len(vals), 2):
                    vals[i] = round(vals[i] * k)
                    vals[i + 1] = round((vals[i + 1] - mid) * k + mid_box)
                return (seg[0], *vals)
            cs = [[tf(seg) for seg in c] for c in cs]
            width = round(adv * k)
            n = max(1, math.ceil(width / 30000))
            w = math.ceil(width / n)
            whole = _to_path(cs)
            whole.simplify(fix_winding=True)
            chars = ""
            for j in range(n):
                rect = pathops.Path()
                x0, x1 = j * w, min(width, (j + 1) * w)
                rect.moveTo(x0, -60000); rect.lineTo(x1, -60000); rect.lineTo(x1, 60000); rect.lineTo(x0, 60000)
                rect.close()
                piece = _from_path(pathops.op(whole, rect, pathops.PathOp.INTERSECTION))
                piece = [[(seg[0], *[v - x0 if i % 2 == 0 else v for i, v in enumerate(seg[1:])]) for seg in c]
                         for c in piece]
                glyphs[next_code] = (swf_font.encode_shape(piece), x1 - x0)
                chars += chr(next_code)
                next_code += 1
            by_text[text] = chars
            info[text] = {"chars": [f"U+{ord(c):04X}" for c in chars], "width": width}
        codes[sid] = by_text[text]
    return codes, glyphs, info


# Arabic glyphs added to the game font beyond the ones the texts need (so later drafts work).
EXTRA_CODES = ([0x060C, 0x061B, 0x061F, 0x0640, 0x066A, 0x066B, 0x066C, 0x0670]
               + list(range(0x0621, 0x063B)) + list(range(0x0641, 0x0653))
               + list(range(0x0660, 0x066A)) + list(range(0xFE70, 0xFE75))
               + list(range(0xFE76, 0xFEFD)))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fill_sources(rows, refs) -> None:
    """The Arabic-only draft (translation/arabic_player_draft_ar.csv, the one in git) has no
    English column: take each row's English from the player's own game files."""
    for r in rows:
        if r.get("source") is None:
            try:
                fmg = refs[archive_name(r["source_file"])][int(r["bnd_entry_id"])]
                r["source"] = fmg[1].get(int(r["entry_id"])) if isinstance(fmg, tuple) else fmg.get(int(r["entry_id"]))
            except (KeyError, ValueError):
                r["source"] = None
            if r["source"] is None:
                r["source"] = ""


def default_draft() -> Path:
    full = ROOT / "translation" / "arabic_player_draft.csv"
    return full if full.is_file() else ROOT / "translation" / "arabic_player_draft_ar.csv"


def load_draft(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def archive_name(source_file: str) -> str:
    stem = source_file.replace("\\", "/").rsplit("/", 1)[-1]
    if stem not in ("item.msgbnd.dcx", "menu.msgbnd.dcx"):
        raise ValueError(f"unexpected source_file {source_file!r}")
    if f"/msg/{LOCALE}/" not in source_file.replace("\\", "/"):
        raise ValueError(f"source_file not in msg/{LOCALE}: {source_file!r}")
    return stem.split(".")[0]


def drop_banners(plan, issues) -> int:
    n = 0
    for arc, fid, sid in BANNER_IDS:
        if plan.get(arc, {}).get(fid, {}).pop(sid, None) is not None:
            issues.append((f"bb:{LOCALE}:ui:bnd{fid}:{sid}", "kept_english_banner",
                           "centre-screen banner: English kept (use --arabic-banners to show the Arabic)"))
            n += 1
    return n


def plan_rows(rows, originals, issues, strip_harakat=False):
    """-> {archive: {fmg id: {string id: new text}}}, stats. Checks every row against the game."""
    plan = {a: {} for a in ARCHIVES}
    stats = Counter()
    seen = set()
    for r in rows:
        rid, status = r["id"], r["status"]
        try:
            arc = archive_name(r["source_file"])
            fmg, sid = int(r["bnd_entry_id"]), int(r["entry_id"])
        except (ValueError, KeyError) as e:
            issues.append((rid, "mapping", str(e)))
            stats["skipped_bad_mapping"] += 1
            continue
        expected_id_tail = f":bnd{fmg}:{sid}"
        if not rid.endswith(expected_id_tail) or not rid.startswith(f"bb:{LOCALE}:"):
            issues.append((rid, "mapping", f"id does not match bnd {fmg} / entry {sid}"))
            stats["skipped_bad_mapping"] += 1
            continue
        if (arc, fmg, sid) in seen:
            issues.append((rid, "mapping", "duplicate row for the same string"))
            stats["skipped_duplicate"] += 1
            continue
        seen.add((arc, fmg, sid))
        groups_strings = originals[arc].get(fmg)
        if groups_strings is None or sid not in groups_strings[1]:
            issues.append((rid, "mapping", f"no string {sid} in FMG {fmg} of {arc}"))
            stats["skipped_missing_in_game"] += 1
            continue
        game_text = groups_strings[1][sid]
        if game_text != r["source"]:
            issues.append((rid, "source_mismatch", "text in the base archive differs from the CSV source "
                           "(changed by a bbport layer?); base text kept"))
            stats["skipped_source_mismatch"] += 1
            continue
        tr = r["translation"]
        if status in DRAFT_STATUSES:
            if not tr.strip():
                stats["draft_empty_kept_source"] += 1
                continue
            if Counter(find_tokens(tr)) != Counter(find_tokens(r["source"])):
                issues.append((rid, "tokens", "codes differ from the source; row kept in English"))
                stats["draft_token_mismatch_kept_source"] += 1
                continue
            if tr.count("\n") != r["source"].count("\n"):
                issues.append((rid, "line_breaks", f"source {r['source'].count(chr(10))} / "
                               f"translation {tr.count(chr(10))} line breaks (written as is)"))
            if unicodedata.normalize("NFC", tr) != tr:
                issues.append((rid, "not_nfc", "translation is not NFC (written as is)"))
            plan[arc].setdefault(fmg, {})[sid] = shape_for_display(tr, delete_harakat=strip_harakat)
            stats["draft_written"] += 1
        elif status in LITERAL_STATUSES:
            plan[arc].setdefault(fmg, {})[sid] = tr if tr else r["source"]
            stats["preserved_written"] += 1
        elif status.startswith(KEEP_SOURCE_PREFIX):
            stats[f"{status}_kept_source"] += 1
        else:
            issues.append((rid, "status", f"unknown status {status!r}; kept in English"))
            stats["unknown_status_kept_source"] += 1
    return plan, stats


def read_archive(path: Path):
    raw = path.read_bytes()
    bnd = fromsoft.unpack_dcx(raw)
    return raw, {fid: fromsoft.read_fmg(data) for fid, data in fromsoft.bnd4_contents(bnd).items()}


def load_extras(path):
    """Arabic for texts bbport adds in its own layers (PC Settings, Rebirth, Seamless co-op
    bells, Quit to Desktop): archive, fmg, entry_id, source, translation, status."""
    if not path:
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [r for r in csv.DictReader(f) if r["status"] == "draft" and r["translation"].strip()]


def build_locale(loc, game, bbport_out, plan, extras, issues, stats, strip_harakat=False):
    """The two archives for one text folder (engus / enggb): bbport's layer files as the base
    when there are any (they already hold Quit to Desktop, PC Settings, Rebirth, the bells),
    else the game's. The plan (checked against engus) is applied by id; a string that bbport
    changed in its layer keeps bbport's text, and the extras translate those by their English."""
    layer = {"menu": "rebirth", "item": "bbsc_items"}
    out, info = {}, {}
    for arc in ARCHIVES:
        game_path = game / "msg" / loc / f"{arc}.msgbnd.dcx"
        base_path = None
        if bbport_out:
            base_path = bbport_out / layer[arc] / "dvdroot_ps4" / "msg" / loc / f"{arc}.msgbnd.dcx"
            if not base_path.is_file():
                base_path = None
        _, game_fmgs = read_archive(game_path)
        raw, base = read_archive(base_path or game_path)
        changes, added = {}, set()
        kept_bbport = 0
        for fid, texts in plan[arc].items():
            g = game_fmgs.get(fid, (None, {}))[1]
            bt = base.get(fid, (None, {}))[1]
            for sid, text in texts.items():
                if sid not in bt:
                    issues.append((f"{loc}:{arc}:fmg{fid}:{sid}", "missing_in_locale", "id not in this text folder"))
                    continue
                if sid in g and bt[sid] != g[sid]:
                    kept_bbport += 1  # changed by a bbport layer: its text stays (extras below)
                    continue
                changes.setdefault(fid, {})[sid] = text
        quit_or_extra = 0
        if base_path is None and arc == "menu":  # no bbport layer: Quit to Desktop by id, like quit_menu.py
            for (fid, sid), text in QUIT_TEXTS.items():
                if sid not in base[fid][1]:
                    added.add(fid)
                changes.setdefault(fid, {})[sid] = shape_for_display(text, delete_harakat=strip_harakat)
                quit_or_extra += 1
        for r in extras:
            if r["archive"] != arc:
                continue
            fid, sid = int(r["fmg"]), int(r["entry_id"])
            if base.get(fid, (None, {}))[1].get(sid) == r["source"]:
                if Counter(find_tokens(r["translation"])) != Counter(find_tokens(r["source"])):
                    issues.append((f"extra:{arc}:fmg{fid}:{sid}", "tokens", "codes differ; kept in English"))
                    continue
                changes.setdefault(fid, {})[sid] = shape_for_display(r["translation"], delete_harakat=strip_harakat)
                quit_or_extra += 1
        bnd = fromsoft.unpack_dcx(raw)
        contents = fromsoft.bnd4_contents(bnd)
        replace = {}
        for fid, ch in changes.items():
            groups, strings = base[fid]
            new_strings = dict(strings)
            new_strings.update(ch)
            replace[fid] = fromsoft.write_fmg(contents[fid], None if fid in added else groups, new_strings)
        out[arc] = fromsoft.pack_dcx(raw, fromsoft.rebuild_bnd4(bnd, replace))
        info[arc] = {"base": str(base_path or game_path), "base_sha256": sha256(raw),
                     "strings_written": sum(len(c) for c in changes.values()),
                     "kept_bbport_text": kept_bbport, "bbport_and_quit_texts_translated": quit_or_extra,
                     "ids_added": sorted(added)}
        stats[f"{loc}_{arc}_written"] = info[arc]["strings_written"]
    return out, info


def build_font(game_font: bytes, ttf_path: Path, needed: set[int], scale: float, extra_glyphs=None):
    from fontTools.ttLib import TTFont

    head, pos, ln = swf_font.find_font3(game_font)
    font = swf_font.parse_font3(game_font[pos:pos + ln])
    if swf_font.build_font3(font) != game_font[pos:pos + ln]:
        raise ValueError("DefineFont3 does not round-trip; refusing to edit it")
    have = set(font.codes)
    tt = TTFont(str(ttf_path))
    cmap = tt.getBestCmap()
    gs = tt.getGlyphSet()
    hmtx = tt["hmtx"]
    extra_glyphs = extra_glyphs or {}
    needed = set(needed) - set(extra_glyphs)
    want = sorted((needed | set(EXTRA_CODES)) - have)
    missing = [c for c in want if c not in cmap]
    want = [c for c in want if c in cmap]
    missing_needed = [c for c in missing if c in needed]
    if missing_needed:
        raise ValueError("font lacks glyphs the texts need: " + ", ".join(f"U+{c:04X}" for c in missing_needed))
    # Outer-contour winding of the game glyphs (taken from 'H').
    game_sign = swf_font.signed_area(max(swf_font.decode_shape(font.shapes[font.codes.index(ord("H"))]),
                                         key=lambda c: abs(swf_font.signed_area(c))))
    glyphs = {}
    for code in want:
        name = cmap[code]
        contours = swf_font.orient_like_game(swf_font.ttf_glyph_contours(gs, name, scale), game_sign)
        advance = round(hmtx[name][0] * scale)
        if unicodedata.category(chr(code)) == "Mn":
            advance = 0  # combining marks sit on the next glyph in visual order
        glyphs[code] = (swf_font.encode_shape(contours), advance)
    glyphs.update(extra_glyphs)
    new_font = swf_font.add_glyphs(font, glyphs)
    body = swf_font.build_font3(new_font)
    out = swf_font.replace_tag(game_font, head, pos, ln, 75, body)
    info = {"game_glyphs": len(font.codes), "added_glyphs": len(glyphs), "total_glyphs": len(new_font.codes),
            "skipped_optional_codes_not_in_ttf": [f"U+{c:04X}" for c in missing], "scale": scale}
    return out, info


def line_width(text: str, adv: dict) -> int:
    """Pen advance of one line in font units; markup codes count as zero (icons vary)."""
    from common.placeholders import _COMBINED
    return sum(adv.get(ord(c), 0) for c in _COMBINED.sub("", text))


def wrap_risks(rows, plan, font_bytes, issues, factor=1.15):
    """Flag multi-line strings whose Arabic line is clearly wider than the widest English line:
    the game box was sized for English, so Scaleform may wrap it (a wrapped visual-order line
    reads bottom-to-top). Information for testers only; nothing is changed."""
    head, pos, ln = swf_font.find_font3(font_bytes)
    f = swf_font.parse_font3(font_bytes[pos:pos + ln])
    adv = dict(zip(f.codes, f.advances))
    n = 0
    for r in rows:
        if r["status"] != "draft" or "\n" not in r["source"]:
            continue
        got = plan[archive_name(r["source_file"])].get(int(r["bnd_entry_id"]), {}).get(int(r["entry_id"]))
        if got is None:
            continue
        en = max(line_width(x, adv) for x in r["source"].split("\n"))
        ar = max(line_width(x, adv) for x in got.split("\n"))
        if en and ar > en * factor:
            issues.append((r["id"], "wrap_risk", f"widest Arabic line {ar / en:.0%} of the widest English line"))
            n += 1
    return n


def tool_versions() -> dict:
    from importlib.metadata import version, PackageNotFoundError
    out = {"python": sys.version.split()[0]}
    for pkg in ("arabic-reshaper", "python-bidi", "fonttools"):
        try:
            out[pkg] = version(pkg)
        except PackageNotFoundError:
            out[pkg] = None
    return out


def text_chars(plan) -> set[int]:
    return {ord(ch) for arc in plan.values() for fmg in arc.values() for t in fmg.values() if t
            for ch in t if ch not in "\n\r"}


README_AR = """مود تجريبي لتعريب Bloodborne على bbport (مسودة — غير معتمدة)
==================================================================
هذا المود مبني من مسودة الترجمة (draft). لم تُعتمد أي ترجمة، والحالات في ملف CSV كما هي.

التركيب:
1. انسخي مجلد {name} كاملاً إلى مجلد mods داخل مجلد bbport-windows الذي تشغّلين منه اللعبة:
     <bbport-windows>\\mods\\{name}\\dvdroot_ps4\\...
   (إذا ما في مجلد mods أنشئيه.)
2. افتحي المشغّل (Play Bloodborne.exe) ← صفحة "Mods & patches" ← فعّلي {name}.
3. في "Game & effects" اختاري Text language = English (المود يستبدل ملفات engus و enggb).
4. شغّلي اللعبة.

الإزالة:
- عطّلي المود من "Mods & patches"، أو احذفي مجلد mods\\{name}. ملفات اللعبة الأصلية لا تتغير أبداً.

الملفات:
- dvdroot_ps4/msg/engus و enggb : item.msgbnd.dcx و menu.msgbnd.dcx = النصوص العربية (مشكّلة ومرتّبة للعرض)
- dvdroot_ps4/menu/engus و enggb : font.gfx : خط اللعبة الأصلي مع حروف عربية مضافة (من خط Amiri، رخصة SIL OFL 1.1)
- build_report.json : تفاصيل البناء والبصمات
- issues_by_id.csv : الملاحظات حسب المعرّف
"""


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--game", type=Path, default=ROOT / "game" / "CUSA03173" / "dvdroot_ps4",
                   help="original dvdroot_ps4 (read only)")
    p.add_argument("--draft", type=Path, default=None,
                   help="draft CSV; default: translation/arabic_player_draft.csv if present, else the "
                        "Arabic-only translation/arabic_player_draft_ar.csv")
    p.add_argument("--font", type=Path, required=True, help="TrueType Arabic font (glyf outlines)")
    p.add_argument("--font-license", type=Path, help="licence file copied next to the mod's font")
    p.add_argument("--out", type=Path, default=ROOT / "mod_builder" / "output")
    p.add_argument("--name", default="BloodborneArabic_test")
    p.add_argument("--scale", type=float, default=19.0,
                   help="font units -> game units (game em = 20480); default 19 for a 1000-unit font")
    p.add_argument("--bbport-out", type=Path, help="bbport's out folder (after one game start): its "
                   "rebirth/ and bbsc_items/ text layers become the base, so Quit to Desktop, PC Settings, "
                   "Rebirth and the Seamless co-op bells stay")
    p.add_argument("--extras", type=Path, default=ROOT / "translation" / "bbport_extras_draft.csv",
                   help="Arabic draft for bbport's added texts (applied where the base has that English text)")
    p.add_argument("--locales", default="engus,enggb", help="text folders to write (same ids, same font)")
    p.add_argument("--banner-style", choices=["drawn", "english", "arabic"], default=None,
                   help="centre-screen banners (YOU DIED, ...): drawn = whole phrase as one glyph in "
                        "--banner-font (default when that font exists), english, or arabic (plain glyphs)")
    p.add_argument("--banner-font", type=Path, default=ROOT / "fonts" / "Jomhuria-Regular.ttf")
    p.add_argument("--banner-texts", type=Path, default=ROOT / "translation" / "banners_ar.csv")
    p.add_argument("--banner-size", type=float, default=1.5,
                   help="drawn banner letter height / the English capitals' height (1.0 = as tall)")
    p.add_argument("--banner-offset", type=float, default=0,
                   help="move drawn banners down (+) or up (-), in font units (20480 = one line height)")
    p.add_argument("--no-banner-drips", action="store_true", help="drawn banners without blood drips")
    p.add_argument("--strip-harakat", action="store_true", help="drop harakat in the DISPLAY copy only")
    a = p.parse_args(argv)
    a.draft = a.draft or default_draft()
    if a.extras and not a.extras.is_file():
        a.extras = None

    game, out_root = a.game.resolve(), (a.out / a.name).resolve()
    if out_root == game or game in out_root.parents or out_root in game.parents:
        raise SystemExit("--out must be outside the game folder")
    if out_root.exists() and any(out_root.iterdir()):
        raise SystemExit(f"{out_root} exists and is not empty; choose another --name or move it away")

    rows = load_draft(a.draft)
    issues: list[tuple[str, str, str]] = []
    refs = {arc: read_archive(game / "msg" / LOCALE / f"{arc}.msgbnd.dcx")[1] for arc in ARCHIVES}
    fill_sources(rows, refs)
    plan, stats = plan_rows(rows, refs, issues, a.strip_harakat)
    banner_extra, banner_info = {}, {}
    style = a.banner_style or ("drawn" if a.banner_font.is_file() and a.banner_texts.is_file() else "english")
    if style == "english":
        stats["banners_kept_english"] = drop_banners(plan, issues)
    elif style == "drawn":
        banner_codes, banner_extra, banner_info = banner_glyphs(load_banner_texts(a.banner_texts), a.banner_font,
                                                                not a.no_banner_drips, a.banner_size,
                                                                a.banner_offset)
        for sid, ch in banner_codes.items():
            if ("menu", 200, sid) in BANNER_IDS:
                plan["menu"].setdefault(200, {})[sid] = ch
        stats["banners_drawn"] = len(banner_codes)
    extras = load_extras(a.extras)
    locales = [x.strip() for x in a.locales.split(",") if x.strip()]
    built, locale_info = {}, {}
    for loc in locales:
        built[loc], locale_info[loc] = build_locale(loc, game, a.bbport_out, plan, extras, issues, stats,
                                                    a.strip_harakat)
    used = set()
    for loc in built:
        for data in built[loc].values():
            for d in fromsoft.bnd4_contents(fromsoft.unpack_dcx(data)).values():
                for t in fromsoft.read_fmg(d)[1].values():
                    if t:
                        used |= {ord(c) for c in t if c not in "\n\r"}
    game_font = (game / "menu" / LOCALE / "font.gfx").read_bytes()
    font_bytes, font_info = build_font(game_font, a.font, used, a.scale, banner_extra)
    font_info["wrap_risk_rows"] = wrap_risks(rows, plan, font_bytes, issues)

    outputs = {}
    for loc in locales:
        loc_font = game / "menu" / loc / "font.gfx"
        if loc != LOCALE and sha256(loc_font.read_bytes()) != sha256(game_font):
            raise SystemExit(f"menu/{loc}/font.gfx differs from engus's; build that locale's font separately")
        msg_dir = out_root / "dvdroot_ps4" / "msg" / loc
        menu_dir = out_root / "dvdroot_ps4" / "menu" / loc
        msg_dir.mkdir(parents=True, exist_ok=True)
        menu_dir.mkdir(parents=True, exist_ok=True)
        for arc, data in built[loc].items():
            path = msg_dir / f"{arc}.msgbnd.dcx"
            path.write_bytes(data)
            outputs[path.relative_to(out_root).as_posix()] = sha256(data)
        (menu_dir / "font.gfx").write_bytes(font_bytes)
        outputs[(menu_dir / "font.gfx").relative_to(out_root).as_posix()] = sha256(font_bytes)
    if a.font_license:
        (out_root / ("FONT_LICENSE_" + a.font_license.name)).write_bytes(a.font_license.read_bytes())
    banner_licence = a.banner_font.parent / "OFL-Jomhuria.txt"
    if style == "drawn" and banner_licence.is_file():
        (out_root / ("FONT_LICENSE_" + banner_licence.name)).write_bytes(banner_licence.read_bytes())

    with open(out_root / "issues_by_id.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "kind", "note"])
        w.writerows(issues)
    report = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kind": "TEST BUILD FROM DRAFT - nothing approved",
        "draft_csv": {"path": str(a.draft), "sha256": sha256(a.draft.read_bytes()), "rows": len(rows),
                      "statuses": dict(Counter(r["status"] for r in rows))},
        "inputs_sha256": {f"menu/{LOCALE}/font.gfx": sha256(game_font), "font": sha256(a.font.read_bytes()),
                          "extras_csv": sha256(a.extras.read_bytes()) if a.extras else None},
        "locales": locale_info,
        "font_file": a.font.name,
        "strip_harakat": a.strip_harakat,
        "banner_style": style,
        "banner_glyphs": banner_info,
        "banner_codes": {str(k): v for k, v in (banner_codes.items() if style == "drawn" else [])},
        "stats": dict(stats),
        "font": font_info,
        "bbport_out": str(a.bbport_out) if a.bbport_out else None,
        "extras_csv": str(a.extras) if a.extras else None,
        "quit_texts_without_bbport_layer": {f"fmg{k[0]}:{k[1]}": v for k, v in QUIT_TEXTS.items()},
        "tool_versions": tool_versions(),
        "outputs_sha256": outputs,
        "issues_by_kind": dict(Counter(k for _, k, _ in issues)),
    }
    (out_root / "build_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_root / "README_AR.txt").write_text(README_AR.format(name=a.name), encoding="utf-8-sig")
    print(json.dumps({"out": str(out_root), "stats": report["stats"], "font": font_info,
                      "issues": report["issues_by_kind"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
