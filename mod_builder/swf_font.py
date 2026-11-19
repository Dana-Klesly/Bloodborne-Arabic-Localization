"""Add glyphs to the DefineFont3 inside Bloodborne's Scaleform font file (menu/<lang>/font.gfx).

The game's font ("TT-Reimin Y10 B", tag 75, 7652 glyphs) has Latin, Cyrillic and CJK but no
Arabic. Scaleform draws one glyph per code with no shaping and no bidi, so the text we write
is already in presentation forms and visual order; here we only add the glyphs for those codes.

Glyph units: em = 1024 * 20 = 20480 (ascent 18020 + descent 2460), y grows downwards.
The game's glyph shapes use 1 fill bit / 0 line bits and FillStyle0 = 1; we write the same.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field


# ---------- SWF container ----------
def swf_tags(data: bytes):
    """Yield (code, header position, body position, body length) of each top-level tag."""
    if data[:3] not in (b"GFX", b"FWS"):
        raise ValueError("expected an uncompressed GFX/SWF file")
    nbits = data[8] >> 3
    pos = 8 + (5 + 4 * nbits + 7) // 8 + 4
    while pos < len(data):
        head = pos
        code_len = struct.unpack_from("<H", data, pos)[0]
        pos += 2
        code, ln = code_len >> 6, code_len & 0x3F
        if ln == 0x3F:
            ln = struct.unpack_from("<I", data, pos)[0]
            pos += 4
        yield code, head, pos, ln
        pos += ln
        if code == 0:
            break


def long_tag(code: int, body: bytes) -> bytes:
    return struct.pack("<HI", (code << 6) | 0x3F, len(body)) + body


def replace_tag(data: bytes, head: int, body_pos: int, ln: int, code: int, body: bytes) -> bytes:
    out = bytearray(data[:head] + long_tag(code, body) + data[body_pos + ln:])
    struct.pack_into("<I", out, 4, len(out))
    return bytes(out)


# ---------- DefineFont3 ----------
@dataclass
class Font3:
    fid: int
    flags: int
    lang: int
    name: bytes
    shapes: list
    codes: list
    ascent: int = 0
    descent: int = 0
    leading: int = 0
    advances: list = field(default_factory=list)
    bounds: list = field(default_factory=list)   # raw RECT bytes per glyph
    kerning: list = field(default_factory=list)  # (code1, code2, adjustment)
    tail: bytes = b""


def _rect_len(d: bytes, p: int) -> int:
    return (5 + 4 * (d[p] >> 3) + 7) // 8


def parse_font3(body: bytes) -> Font3:
    d = body
    fid, flags, lang, nlen = struct.unpack_from("<HBBB", d, 0)
    name = d[5:5 + nlen]
    p = 5 + nlen
    n = struct.unpack_from("<H", d, p)[0]
    p += 2
    if not (flags & 0x08 and flags & 0x04 and flags & 0x80):
        raise ValueError("expected wide offsets, wide codes and layout")
    base = p
    offs = list(struct.unpack_from("<%dI" % n, d, p))
    p += 4 * n
    code_off = struct.unpack_from("<I", d, p)[0]
    ends = offs[1:] + [code_off]
    shapes = [d[base + a:base + b] for a, b in zip(offs, ends)]
    p = base + code_off
    codes = list(struct.unpack_from("<%dH" % n, d, p))
    p += 2 * n
    f = Font3(fid, flags, lang, name, shapes, codes)
    f.ascent, f.descent, f.leading = struct.unpack_from("<HHh", d, p)
    p += 6
    f.advances = list(struct.unpack_from("<%dh" % n, d, p))
    p += 2 * n
    for _ in range(n):
        ln = _rect_len(d, p)
        f.bounds.append(d[p:p + ln])
        p += ln
    kc = struct.unpack_from("<H", d, p)[0]
    p += 2
    for _ in range(kc):
        f.kerning.append(struct.unpack_from("<HHh", d, p))
        p += 6
    f.tail = d[p:]
    return f


def build_font3(f: Font3) -> bytes:
    n = len(f.shapes)
    if not (len(f.codes) == len(f.advances) == len(f.bounds) == n):
        raise ValueError("glyph arrays differ in length")
    if f.codes != sorted(set(f.codes)):
        raise ValueError("glyph codes must be unique and sorted")
    if n > 0xFFFF:
        raise ValueError("too many glyphs")
    out = bytearray(struct.pack("<HBBB", f.fid, f.flags, f.lang, len(f.name)) + f.name)
    out += struct.pack("<H", n)
    acc = 4 * n + 4
    offs = []
    for s in f.shapes:
        offs.append(acc)
        acc += len(s)
    out += struct.pack("<%dI" % n, *offs) + struct.pack("<I", acc)
    for s in f.shapes:
        out += s
    out += struct.pack("<%dH" % n, *f.codes)
    out += struct.pack("<HHh", f.ascent, f.descent, f.leading)
    out += struct.pack("<%dh" % n, *f.advances)
    for b in f.bounds:
        out += b
    out += struct.pack("<H", len(f.kerning))
    for k in f.kerning:
        out += struct.pack("<HHh", *k)
    out += f.tail
    return bytes(out)


# ---------- SHAPE records ----------
class _BitW:
    def __init__(self):
        self.bits = []

    def u(self, v, n):
        for i in range(n - 1, -1, -1):
            self.bits.append((v >> i) & 1)

    def s(self, v, n):
        self.u(v & ((1 << n) - 1), n)

    def done(self) -> bytes:
        b = self.bits + [0] * (-len(self.bits) % 8)
        return bytes(int("".join(map(str, b[i:i + 8])), 2) for i in range(0, len(b), 8))


class _BitR:
    def __init__(self, d):
        self.d, self.p = d, 0

    def u(self, n):
        v = 0
        for _ in range(n):
            v = (v << 1) | ((self.d[self.p >> 3] >> (7 - (self.p & 7))) & 1)
            self.p += 1
        return v

    def s(self, n):
        v = self.u(n)
        return v - (1 << n) if n and v >> (n - 1) else v


def _nbits(*vals) -> int:
    m = 1
    for v in vals:
        v = int(v)
        m = max(m, (v if v >= 0 else ~v).bit_length() + 1)
    return m


def decode_shape(shape: bytes) -> list:
    """-> contours: lists of ('M',x,y) then ('L',x,y) / ('Q',cx,cy,x,y), absolute."""
    r = _BitR(shape)
    fb, lb = r.u(4), r.u(4)
    x = y = 0
    out = []
    while True:
        if r.u(1) == 0:
            fl = r.u(5)
            if fl == 0:
                break
            if fl & 1:
                n = r.u(5)
                x, y = r.s(n), r.s(n)
                out.append([("M", x, y)])
            if fl & 2:
                r.u(fb)
            if fl & 4:
                r.u(fb)
            if fl & 8:
                r.u(lb)
            if fl & 16:
                raise ValueError("new styles inside a glyph")
        elif r.u(1):
            n = r.u(4) + 2
            if r.u(1):
                dx, dy = r.s(n), r.s(n)
            elif r.u(1):
                dx, dy = 0, r.s(n)
            else:
                dx, dy = r.s(n), 0
            x, y = x + dx, y + dy
            out[-1].append(("L", x, y))
        else:
            n = r.u(4) + 2
            cx, cy = x + r.s(n), y + r.s(n)
            x, y = cx + r.s(n), cy + r.s(n)
            out[-1].append(("Q", cx, cy, x, y))
    return out


def encode_shape(contours: list) -> bytes:
    w = _BitW()
    w.u(1, 4)
    w.u(0, 4)
    x = y = 0
    first = True
    for c in contours:
        _, mx, my = c[0]
        w.u(0, 1)
        w.u(0b00101 if first else 0b00001, 5)  # MoveTo (+ FillStyle0 on the first record)
        n = _nbits(mx, my)
        w.u(n, 5)
        w.s(mx, n)
        w.s(my, n)
        if first:
            w.u(1, 1)
            first = False
        x, y = mx, my
        for seg in c[1:]:
            if seg[0] == "L":
                dx, dy = seg[1] - x, seg[2] - y
                if dx == 0 and dy == 0:
                    continue
                n = max(2, _nbits(dx, dy))
                w.u(0b11, 2)
                w.u(n - 2, 4)
                if dx and dy:
                    w.u(1, 1)
                    w.s(dx, n)
                    w.s(dy, n)
                elif dx == 0:
                    w.u(0b01, 2)
                    w.s(dy, n)
                else:
                    w.u(0b00, 2)
                    w.s(dx, n)
                x, y = seg[1], seg[2]
            else:
                cdx, cdy = seg[1] - x, seg[2] - y
                adx, ady = seg[3] - seg[1], seg[4] - seg[2]
                n = max(2, _nbits(cdx, cdy, adx, ady))
                w.u(0b10, 2)
                w.u(n - 2, 4)
                for v in (cdx, cdy, adx, ady):
                    w.s(v, n)
                x, y = seg[3], seg[4]
        if (x, y) != (mx, my):  # close the contour explicitly
            dx, dy = mx - x, my - y
            n = max(2, _nbits(dx, dy))
            w.u(0b11, 2)
            w.u(n - 2, 4)
            w.u(1, 1)
            w.s(dx, n)
            w.s(dy, n)
    w.u(0, 6)  # EndShapeRecord
    return w.done()


def signed_area(contour) -> float:
    pts = [(s[-2], s[-1]) for s in contour]
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1])) / 2


# ---------- TrueType -> SWF glyphs ----------
def ttf_glyph_contours(glyph_set, glyph_name: str, scale: float) -> list:
    """Quadratic TrueType outline -> SWF contours (y flipped, scaled, integer units)."""
    from fontTools.pens.recordingPen import DecomposingRecordingPen

    pen = DecomposingRecordingPen(glyph_set)
    glyph_set[glyph_name].draw(pen)

    def P(pt):
        return (round(pt[0] * scale), round(-pt[1] * scale))

    contours, cur, start = [], None, None
    for op, args in pen.value:
        if op == "moveTo":
            start = args[0]
            cur = [("M",) + P(start)]
            last = start
        elif op == "lineTo":
            cur.append(("L",) + P(args[0]))
            last = args[0]
        elif op == "qCurveTo":
            pts = list(args)
            if pts[-1] is None:  # closed contour of off-curve points only
                pts = pts[:-1]
                mid = ((pts[-1][0] + pts[0][0]) / 2, (pts[-1][1] + pts[0][1]) / 2)
                cur = [("M",) + P(mid)]
                last = mid
                pts = pts + [mid]
            offs, on = pts[:-1], pts[-1]
            for i, c in enumerate(offs):
                end = on if i == len(offs) - 1 else ((c[0] + offs[i + 1][0]) / 2, (c[1] + offs[i + 1][1]) / 2)
                cur.append(("Q",) + P(c) + P(end))
                last = end
        elif op == "curveTo":
            raise ValueError("cubic outlines are not supported; use a TrueType (glyf) font")
        elif op in ("closePath", "endPath"):
            if cur and len(cur) > 1:
                contours.append(cur)
            cur = None
    return contours


def orient_like_game(contours: list, game_outer_sign: float) -> list:
    """Reverse every contour if the font's outer contours wind opposite to the game's."""
    if not contours:
        return contours
    biggest = max(contours, key=lambda c: abs(signed_area(c)))
    if (signed_area(biggest) > 0) == (game_outer_sign > 0):
        return contours
    out = []
    for c in contours:
        pts = [("M", c[0][1], c[0][2])] + list(c[1:])
        # walk the segments backwards
        rev = [("M", pts[-1][-2], pts[-1][-1])]
        for i in range(len(pts) - 1, 0, -1):
            prev_end = (pts[i - 1][-2], pts[i - 1][-1])
            seg = pts[i]
            if seg[0] == "L":
                rev.append(("L",) + prev_end)
            else:
                rev.append(("Q", seg[1], seg[2]) + prev_end)
        out.append(rev)
    return out


def add_glyphs(font: Font3, glyphs: dict) -> Font3:
    """glyphs: {code: (shape bytes, advance)}. Existing codes are kept untouched (error if a
    code is already present). Returns a new Font3 with everything sorted by code."""
    clash = set(glyphs) & set(font.codes)
    if clash:
        raise ValueError(f"codes already in the game font: {sorted(clash)[:10]}")
    empty_rect = font.bounds[0] if font.bounds else b"\x08\x00"
    rows = list(zip(font.codes, font.shapes, font.advances, font.bounds))
    for code, (shape, adv) in glyphs.items():
        rows.append((code, shape, adv, empty_rect))
    rows.sort(key=lambda r: r[0])
    return Font3(font.fid, font.flags, font.lang, font.name,
                 [r[1] for r in rows], [r[0] for r in rows],
                 font.ascent, font.descent, font.leading,
                 [r[2] for r in rows], [r[3] for r in rows], list(font.kerning), font.tail)


def find_font3(data: bytes):
    found = [(head, pos, ln) for code, head, pos, ln in swf_tags(data) if code == 75]
    if len(found) != 1:
        raise ValueError(f"expected one DefineFont3 tag, found {len(found)}")
    return found[0]
