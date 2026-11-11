"""Render Arabic preview images (PNG) to check letter joining, RTL order and fit.

Each card shows:
  EN      the English source
  RAW     the Arabic as stored (logical order, no shaping) drawn naively -> what a game
          shows if it has NO Arabic support (separate letters, wrong direction)
  SHAPED  after arabic-reshaper + python-bidi -> what we would feed the game if it
          cannot shape Arabic itself
Pillow's own shaping (raqm) is switched off on purpose, so the image shows exactly
what our pipeline produces, not what Pillow would fix for us.
"""
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from common.arabic import shape_for_display
from common.paths import FONTS_DIR, ensure_dir

FALLBACK_FONTS = [
    # Windows system fonts with Arabic support (only used if fonts/ has none)
    "C:/Windows/Fonts/tahoma.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/times.ttf",
    # Linux (test machines)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
LATIN_FONTS = [
    "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

BG = (18, 16, 20)
CARD = (34, 30, 36)
TEXT = (232, 226, 214)
MUTED = (150, 140, 130)
ACCENT = (176, 52, 52)
OK = (110, 160, 110)
BOX = (90, 80, 90)


@dataclass
class PreviewItem:
    label: str
    english: str
    arabic: str
    max_px: int | None = None


def find_font(explicit=None) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            raise FileNotFoundError(f"font not found: {p}")
        return p
    local = sorted([*FONTS_DIR.glob("*.ttf"), *FONTS_DIR.glob("*.otf")])
    if local:
        return local[0]
    for f in FALLBACK_FONTS:
        if Path(f).is_file():
            return Path(f)
    raise FileNotFoundError("no Arabic-capable font found; pass --font or put a .ttf in fonts/")


def _latin_font(size):
    for f in LATIN_FONTS:
        if Path(f).is_file():
            return ImageFont.truetype(f, size, layout_engine=ImageFont.Layout.BASIC)
    return ImageFont.load_default()


def _cmap(font_path) -> set[int]:
    from fontTools.ttLib import TTFont
    with TTFont(str(font_path), fontNumber=0, lazy=True) as font:
        return set(font.getBestCmap() or {})


def wrap_logical(text: str, font, max_px: int) -> str:
    """Word-wrap LOGICAL Arabic so each SHAPED line fits max_px. Existing line breaks are kept."""
    out = []
    for para in text.split("\n"):
        line = ""
        for word in para.split(" "):
            trial = f"{line} {word}".strip() if line else word
            if line and font.getlength(shape_for_display(trial)) > max_px:
                out.append(line)
                line = word
            else:
                line = trial
        out.append(line)
    return "\n".join(out)


def render(items: list[PreviewItem], out_path, font_path=None, size: int = 30,
           width: int = 1100, wrap: bool = False) -> dict:
    """Render items into one PNG. Returns info incl. overflow and missing glyphs."""
    font_path = find_font(font_path)
    ar_font = ImageFont.truetype(str(font_path), size, layout_engine=ImageFont.Layout.BASIC)
    small = _latin_font(max(14, size // 2))
    en_font = _latin_font(max(16, int(size * 0.6)))
    cmap = _cmap(font_path)
    asc, desc = ar_font.getmetrics()
    line_h = int((asc + desc) * 1.25)
    small_h = small.getmetrics()[0] + small.getmetrics()[1] + 4
    en_h = en_font.getmetrics()[0] + en_font.getmetrics()[1] + 4
    pad = 24

    prepared = []
    total_h = pad + small_h + pad // 2
    for it in items:
        box_w = min(it.max_px or (width - 2 * pad - 140), width - 2 * pad - 140)
        logical = wrap_logical(it.arabic, ar_font, box_w) if wrap else it.arabic
        shaped_lines = shape_for_display(logical).split("\n")
        raw_lines = logical.split("\n")
        en_lines = (it.english or "").split("\n")
        missing = sorted({c for c in "".join(shaped_lines) + logical
                          if not c.isspace() and ord(c) not in cmap})
        h = pad + small_h + len(en_lines) * en_h + len(raw_lines) * line_h + len(shaped_lines) * line_h + pad
        prepared.append((it, box_w, raw_lines, shaped_lines, en_lines, missing, h))
        total_h += h + pad // 2

    img = Image.new("RGB", (width, total_h + pad), BG)
    d = ImageDraw.Draw(img)
    d.text((pad, pad), f"font: {font_path.name}   size: {size}px   shaping: arabic-reshaper + python-bidi"
                       f"   (Pillow shaping OFF)", font=small, fill=MUTED)
    y = pad + small_h + pad // 2
    result = {"font": str(font_path), "items": []}
    for it, box_w, raw_lines, shaped_lines, en_lines, missing, h in prepared:
        x0, x1 = pad, width - pad
        d.rectangle([x0, y, x1, y + h], fill=CARD)
        cy = y + pad // 2
        overflow = any(ar_font.getlength(l) > box_w for l in shaped_lines)
        status = []
        if overflow:
            status.append("OVERFLOW")
        if missing:
            status.append("MISSING GLYPHS: " + " ".join(f"U+{ord(c):04X}" for c in missing[:8]))
        d.text((x0 + 12, cy), it.label + ("   " + " | ".join(status) if status else "   ok"),
               font=small, fill=ACCENT if status else OK)
        cy += small_h
        for l in en_lines:
            d.text((x0 + 12, cy), l, font=en_font, fill=MUTED)
            cy += en_h
        right = x1 - 16
        left_limit = right - box_w
        for tag, lines, color in (("RAW", raw_lines, MUTED), ("SHAPED", shaped_lines, TEXT)):
            for l in lines:
                d.text((x0 + 12, cy + line_h // 4), tag, font=small, fill=MUTED)
                d.rectangle([left_limit, cy + 2, right, cy + line_h - 2],
                            outline=ACCENT if (tag == "SHAPED" and overflow) else BOX)
                lw = ar_font.getlength(l)
                ty = cy + (line_h - asc - desc) // 2
                if tag == "RAW" and lw > box_w:
                    # clip the naive rendering to its box instead of spilling over the card
                    strip = Image.new("RGB", (int(lw) + 8, line_h), CARD)
                    ImageDraw.Draw(strip).text((4, ty - cy), l, font=ar_font, fill=color)
                    crop = strip.crop((strip.width - box_w + 2, 2, strip.width - 4, line_h - 2))
                    img.paste(crop, (left_limit + 2, cy + 2))
                else:
                    d.text((right - lw - 4, ty), l, font=ar_font, fill=color)
                cy += line_h
        result["items"].append({"label": it.label, "overflow": overflow,
                                "missing_glyphs": [f"U+{ord(c):04X}" for c in missing]})
        y += h + pad // 2

    out_path = Path(out_path)
    ensure_dir(out_path.parent)
    img.save(out_path)
    result["path"] = str(out_path)
    return result
