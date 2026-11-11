"""Check which Arabic characters a font supports."""
from fontTools.ttLib import TTFont

# Basic Arabic letters, harakat, Arabic punctuation and the presentation forms that
# arabic-reshaper produces (needed only if the game cannot shape Arabic itself).
RANGES = {
    "arabic_letters": [*range(0x0621, 0x063B), *range(0x0641, 0x064B)],
    "harakat": [*range(0x064B, 0x0653), 0x0670],
    "arabic_punctuation": [0x060C, 0x061B, 0x061F, 0x066A, 0x066B, 0x066C],
    "arabic_indic_digits": list(range(0x0660, 0x066A)),
    "presentation_forms_b": [*range(0xFE70, 0xFE75), *range(0xFE76, 0xFEFD)],
    "lam_alef_ligatures": list(range(0xFEF5, 0xFEFD)),
}


def coverage(font_path) -> dict:
    with TTFont(str(font_path), fontNumber=0, lazy=True) as font:
        cmap = set(font.getBestCmap() or {})
        name = font["name"].getDebugName(4) if "name" in font else str(font_path)
    report = {"font": name, "path": str(font_path), "groups": {}}
    for group, cps in RANGES.items():
        missing = [cp for cp in cps if cp not in cmap]
        report["groups"][group] = {
            "total": len(cps), "present": len(cps) - len(missing),
            "missing": [f"U+{cp:04X}" for cp in missing],
        }
    return report
