"""Blood drips under a phrase outline (SWF contours, y down): small runs that hang from the
lowest edges of the letters and end in a drop, merged into the outline with skia-pathops."""
from __future__ import annotations

import random


def _to_path(contours):
    import pathops
    p = pathops.Path()
    for c in contours:
        p.moveTo(c[0][1], c[0][2])
        for s in c[1:]:
            if s[0] == "L":
                p.lineTo(s[1], s[2])
            else:
                p.quadTo(s[1], s[2], s[3], s[4])
        p.close()
    return p


def _from_path(path):
    from fontTools.cu2qu import curve_to_quadratic
    contours, cur, last = [], None, None
    for verb, pts in path.segments:
        if verb == "moveTo":
            cur = [("M", round(pts[0][0]), round(pts[0][1]))]
            last = pts[0]
        elif verb == "lineTo":
            cur.append(("L", round(pts[0][0]), round(pts[0][1])))
            last = pts[0]
        elif verb == "qCurveTo":
            offs, on = pts[:-1], pts[-1]
            for i, c in enumerate(offs):
                end = on if i == len(offs) - 1 else ((c[0] + offs[i + 1][0]) / 2, (c[1] + offs[i + 1][1]) / 2)
                cur.append(("Q", round(c[0]), round(c[1]), round(end[0]), round(end[1])))
            last = on
        elif verb == "curveTo":
            q = curve_to_quadratic([last] + list(pts), 4)
            offs, on = q[1:-1], q[-1]
            for i, c in enumerate(offs):
                end = on if i == len(offs) - 1 else ((c[0] + offs[i + 1][0]) / 2, (c[1] + offs[i + 1][1]) / 2)
                cur.append(("Q", round(c[0]), round(c[1]), round(end[0]), round(end[1])))
            last = on
        elif verb in ("closePath", "endPath"):
            if cur and len(cur) > 1:
                contours.append(cur)
            cur = None
    return contours


def _drip(x, top, width, length):
    """A drip hanging from y=top: a wider, rounded neck where it leaves the letter, a thin run,
    and a round drop at the end (pathops Path, y down)."""
    import pathops
    p = pathops.Path()
    w = width / 2
    neck = width * 1.6
    r = width * 1.05
    end = top + length
    p.moveTo(x - neck, top - width * 0.8)
    p.lineTo(x + neck, top - width * 0.8)
    p.quadTo(x + w, top + width * 0.6, x + w, top + width * 1.6)          # neck narrows into the run
    p.lineTo(x + w * 0.9, end - r * 1.2)
    p.quadTo(x + r * 1.1, end - r * 0.6, x + r, end)                       # swelling drop
    p.quadTo(x + r, end + r * 1.1, x, end + r * 1.15)
    p.quadTo(x - r, end + r * 1.1, x - r, end)
    p.quadTo(x - r * 1.1, end - r * 0.6, x - w * 0.9, end - r * 1.2)
    p.lineTo(x - w, top + width * 1.6)
    p.quadTo(x - w, top + width * 0.6, x - neck, top - width * 0.8)
    p.close()
    return p


def _bottoms(path, contours, height, step):
    """[(x, lowest ink y)] sampled across the outline, from the filled path itself."""
    import pathops
    xs = [s[-2] for c in contours for s in c]
    x0, x1 = min(xs), max(xs)
    out = []
    x = x0 + step
    while x < x1 - step:
        best = None
        y = height * 0.4
        # walk upwards until the probe hits ink
        while y > -height * 1.2:
            probe = pathops.Path()
            probe.moveTo(x - 2, y - 2); probe.lineTo(x + 2, y - 2); probe.lineTo(x + 2, y + 2); probe.lineTo(x - 2, y + 2)
            probe.close()
            hit = pathops.op(path, probe, pathops.PathOp.INTERSECTION)
            if list(hit.segments):
                best = y
                break
            y -= height * 0.01
        if best is not None:
            out.append((x, best))
        x += step
    return out


def add_drips(contours, height, count=None, seed=7):
    """Contours with a few blood drips, spread along the bottom of the letters (the baseline
    strokes, kashidas included); `height` is the phrase's font span in the same units."""
    import pathops
    path = _to_path(contours)
    path.simplify(fix_winding=True)
    rnd = random.Random(seed)
    pts = _bottoms(path, contours, height, height * 0.02)
    # only where the ink reaches the baseline band (not under dots or high letters)
    band = [(x, y) for x, y in pts if -height * 0.06 <= y <= height * 0.12]
    if not band:
        return _from_path(path)
    xs = [x for x, _ in band]
    span = xs[-1] - xs[0]
    n = count or max(2, min(4, int(span / (height * 0.9)) + 1))
    chosen = []
    for i in range(n):
        target = xs[0] + span * (i + 0.5) / n + rnd.uniform(-0.08, 0.08) * span / n
        x, y = min(band, key=lambda p: abs(p[0] - target))
        if all(abs(x - c) > height * 0.25 for c, _ in chosen):
            chosen.append((x, y))
    for x, y in chosen:
        width = height * rnd.uniform(0.03, 0.042)
        length = height * rnd.uniform(0.22, 0.5)
        path = pathops.op(path, _drip(x, y, width, length), pathops.PathOp.UNION)
    return _from_path(path)
