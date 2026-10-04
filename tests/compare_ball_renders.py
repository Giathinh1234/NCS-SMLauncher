#!/usr/bin/env python3
"""Compare the desktop and Android NCS balls numerically, not by eye.

Both renders are measured the same way: fraction of pixels actually lit,
luminance distribution, and how much of the frame is warm gold. Eyeballing a
screenshot is how "the ball looks fine" gets said about a ball that is 99%
black.

    python3 tests/compare_ball_renders.py <desktop.png> <android.png> <x0,y0,x1,y1>
"""
import statistics as st
import sys

from PIL import Image


def stats(label, path, box):
    im = Image.open(path).convert("RGB")
    if box:
        im = im.crop(box)
    px = list(im.getdata())
    n = len(px)
    lum = [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in px]
    lit = [p for p in px if sum(p) > 90]
    warm = [p for p in px if p[0] > p[2] + 25]
    sl = sorted(lum)
    out = {
        "label": label,
        "size": im.size,
        "lit": 100 * len(lit) / n,
        "mean": st.mean(lum),
        "p95": sl[int(0.95 * n)],
        "warm": 100 * len(warm) / n,
        "brightest": max(lit, key=sum) if lit else None,
    }
    print(f"  {label}")
    print(f"    size         : {im.size[0]}x{im.size[1]}")
    print(f"    lit fraction : {out['lit']:5.2f}%")
    print(f"    mean lum     : {out['mean']:6.1f}")
    print(f"    p95 lum      : {out['p95']:6.1f}")
    print(f"    warm(gold)   : {out['warm']:5.2f}%")
    if out["brightest"]:
        print(f"    brightest    : rgb{out['brightest']}")
    return out


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    desk = sys.argv[1]
    andr = sys.argv[2]
    box = None
    if len(sys.argv) > 3:
        box = tuple(int(v) for v in sys.argv[3].split(","))

    print("  NCS ball: desktop vs android")
    a = stats("DESKTOP python", desk, None)
    b = stats("ANDROID kotlin", andr, box)
    print()
    print(f"  android is {b['lit'] / max(a['lit'], 0.01):.1f}x as lit "
          f"and {b['mean'] / max(a['mean'], 0.01):.1f}x as bright")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
