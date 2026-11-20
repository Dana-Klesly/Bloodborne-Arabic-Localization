"""A whole Arabic phrase as ONE glyph, shaped with HarfBuzz in a decorative font.

Used for the big centre-screen banners (YOU DIED, ...): the banner text field gets a single
Private-Use character whose glyph is the phrase drawn like a picture, so the game still colours,
fades and centres it as text. The font's own OpenType shaping (ligatures, swashes, mark
positioning) is applied here, which the game's renderer could never do.
"""
from __future__ import annotations

from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont


def shape_phrase(font_path, text: str, features=None):
    """[(glyph name, x offset, y offset)] in font units, and the total advance."""
    import uharfbuzz as hb

    blob = hb.Blob.from_file_path(str(font_path))
    face = hb.Face(blob)
    font = hb.Font(face)
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf, features or {"kern": True, "liga": True, "calt": True})
    tt = TTFont(str(font_path))
    order = tt.getGlyphOrder()
    out, x, y = [], 0, 0
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        out.append((order[info.codepoint], x + pos.x_offset, y + pos.y_offset))
        x += pos.x_advance
        y += pos.y_advance
    return out, x, tt


def phrase_contours(font_path, text: str, height: int, padding: float = 0.06):
    """SWF contours (game units, y down, baseline at 0) of `text`, scaled so the font's
    ascender..descender span is `height` units; returns (contours, advance)."""
    glyphs, advance, tt = shape_phrase(font_path, text)
    hhea = tt["hhea"]
    span = hhea.ascent - hhea.descent
    k = height / span
    gs = tt.getGlyphSet()
    rec = DecomposingRecordingPen(gs)
    pad = advance * padding
    for name, gx, gy in glyphs:
        gs[name].draw(TransformPen(rec, (k, 0, 0, -k, (gx + pad) * k, -gy * k)))
    # rec.value is in game units already (the transform did the scaling and the flip)
    contours, cur = [], None
    last = None
    for op, args in rec.value:
        if op == "moveTo":
            cur = [("M", round(args[0][0]), round(args[0][1]))]
        elif op == "lineTo":
            cur.append(("L", round(args[0][0]), round(args[0][1])))
        elif op == "qCurveTo":
            pts = list(args)
            if pts[-1] is None:
                pts = pts[:-1]
                mid = ((pts[-1][0] + pts[0][0]) / 2, (pts[-1][1] + pts[0][1]) / 2)
                cur = [("M", round(mid[0]), round(mid[1]))]
                pts = pts + [mid]
            offs, on = pts[:-1], pts[-1]
            for i, c in enumerate(offs):
                end = on if i == len(offs) - 1 else ((c[0] + offs[i + 1][0]) / 2, (c[1] + offs[i + 1][1]) / 2)
                cur.append(("Q", round(c[0]), round(c[1]), round(end[0]), round(end[1])))
        elif op == "curveTo":
            raise ValueError("cubic outlines: use a TrueType (glyf) font")
        elif op in ("closePath", "endPath"):
            if cur and len(cur) > 1:
                contours.append(cur)
            cur = None
    return contours, round((advance + 2 * pad) * k)
