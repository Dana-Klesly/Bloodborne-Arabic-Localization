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
    """A drip from y=top down `length`, ending in a round drop (as a pathops Path)."""
    import pathops
    p = pathops.Path()
    w, r = width / 2, width * 0.9
    end = top + length
    p.moveTo(x - w, top - width)
    p.lineTo(x + w, top - width)
    p.quadTo(x + w * 0.6, end - r, x + r, end)      # right side, swelling into the drop
    p.quadTo(x + r, end + r * 1.2, x, end + r * 1.25)
    p.quadTo(x - r, end + r * 1.2, x - r, end)
    p.quadTo(x - w * 0.6, end - r, x - w, top - width)
    p.close()
    return p


def add_drips(contours, height, count=None, seed=7):
    """contours with blood drips; `height` is the phrase's font span in the same units."""
    import pathops
    path = _to_path(contours)
    path.simplify(fix_winding=True)
    xs = [pt for c in contours for s in c for pt in [s[-2]]]
    x0, x1 = min(xs), max(xs)
    rnd = random.Random(seed)
    n = count or max(2, int((x1 - x0) / (height * 0.9)))
    for _ in range(n * 6):
        if n == 0:
            break
        x = rnd.uniform(x0 + height * 0.1, x1 - height * 0.1)
        # lowest ink at this x (sample the outline): find the largest y of segment points near x
        near = [s[-1] for c in contours for s in c if abs(s[-2] - x) < height * 0.03]
        if not near:
            continue
        bottom = max(near)
        if bottom < -height * 0.15:   # only from letters reaching the baseline area
            continue
        width = height * rnd.uniform(0.025, 0.04)
        length = height * rnd.uniform(0.25, 0.5)
        path = pathops.op(path, _drip(x, bottom, width, length), pathops.PathOp.UNION)
        n -= 1
    return _from_path(path)
