"""Generate the Careo wordmark ("careo.") as plain SVG paths (no fonts).

Geometric monoline letters on a shared x-height. Writes:
  assets/careo-wordmark-light.svg  (dark ink, for light backgrounds)
  assets/careo-wordmark-dark.svg   (light ink, for dark backgrounds)
  site/src/components/wordmark-paths.json  (path data for the inline site logo)
"""

import json
import math
from pathlib import Path

R = 9.0  # bowl radius
Y = 18.0  # bowl centre (x-height 9..27)
STROKE = 3.6
GAP = 5.2


def pt(cx, cy, deg):
    a = math.radians(deg)
    return cx + R * math.cos(a), cy - R * math.sin(a)


def arc(cx, start, end, large=1, sweep=0):
    x1, y1 = pt(cx, Y, start)
    x2, y2 = pt(cx, Y, end)
    return f"M{x1:.2f} {y1:.2f}A{R} {R} 0 {large} {sweep} {x2:.2f} {y2:.2f}"


def build():
    paths, x = [], 2.0 + STROKE / 2
    # c
    cx = x + R
    paths.append(arc(cx, 42, -42))
    x = cx + R + GAP
    # a: bowl + stem
    cx = x + R
    paths.append(
        f"M{cx + R:.2f} {Y:.2f}A{R} {R} 0 1 0 {cx - R:.2f} {Y:.2f}A{R} {R} 0 1 0 {cx + R:.2f} {Y:.2f}"
    )
    paths.append(f"M{cx + R:.2f} {Y - R:.2f}V{Y + R:.2f}")
    x = cx + R + GAP
    # r: stem + shoulder
    sx = x
    paths.append(f"M{sx:.2f} {Y - R:.2f}V{Y + R:.2f}")
    paths.append(
        f"M{sx:.2f} {Y + 1:.2f}C{sx:.2f} {Y - 6:.2f} {sx + 4:.2f} {Y - R:.2f} {sx + 10:.2f} {Y - R:.2f}"
    )
    x = sx + 10 + GAP
    # e: bar + open bowl
    cx = x + R
    ex, ey = pt(cx, Y, -42)
    paths.append(f"M{cx - R:.2f} {Y:.2f}H{cx + R:.2f}A{R} {R} 0 1 0 {ex:.2f} {ey:.2f}")
    x = cx + R + GAP
    # o
    cx = x + R
    paths.append(
        f"M{cx + R:.2f} {Y:.2f}A{R} {R} 0 1 0 {cx - R:.2f} {Y:.2f}A{R} {R} 0 1 0 {cx + R:.2f} {Y:.2f}"
    )
    x = cx + R + GAP - 1.2
    dot = (x + 2.4, Y + R - 0.6, 2.6)
    width = dot[0] + dot[2] + 2
    return paths, dot, width


def svg(paths, dot, width, ink, title=True):
    body = "".join(f'<path d="{d}"/>' for d in paths)
    t = "<title>careo.</title>" if title else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.1f} 32" width="{width * 4:.0f}" '
        f'height="128" role="img" aria-label="careo.">{t}'
        f'<g fill="none" stroke="{ink}" stroke-width="{STROKE}" stroke-linecap="round" '
        f'stroke-linejoin="round">{body}</g>'
        f'<circle cx="{dot[0]:.2f}" cy="{dot[1]:.2f}" r="{dot[2]}" fill="{ink}"/></svg>\n'
    )


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    paths, dot, width = build()
    (root / "assets" / "careo-wordmark-light.svg").write_text(svg(paths, dot, width, "#0a0a0a"))
    (root / "assets" / "careo-wordmark-dark.svg").write_text(svg(paths, dot, width, "#f5f5f5"))
    (root / "site" / "public" / "careo-wordmark.svg").write_text(svg(paths, dot, width, "#0a0a0a"))
    (root / "site" / "src" / "components" / "wordmark-paths.json").write_text(
        json.dumps(
            {"paths": paths, "dot": dot, "width": round(width, 1), "stroke": STROKE}, indent=1
        )
        + "\n"
    )
    print("width", round(width, 1))
