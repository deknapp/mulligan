"""Small SVG charts for the findings site, with no plotting dependency.

Every chart is plain SVG meant to be inlined in the page, so the page's CSS
variables (``--fg``, ``--muted``, ``--grid``, the five mana colors) theme it in
light and dark mode. Points and bars carry ``<title>`` elements, which browsers
show as hover tooltips.
"""

from __future__ import annotations

from html import escape

MANA = {"W": "var(--mana-w)", "U": "var(--mana-u)", "B": "var(--mana-b)",
        "R": "var(--mana-r)", "G": "var(--mana-g)", "M": "var(--mana-m)",
        "C": "var(--mana-c)"}


def color_key(colors: str) -> str:
    """One chart color per card: its color, gold for multicolor, gray for none."""
    if not colors:
        return "C"
    return colors if len(colors) == 1 else "M"


def _svg(width: int, height: int, body: list[str], label: str) -> str:
    return (f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="{escape(label)}" xmlns="http://www.w3.org/2000/svg">'
            + "".join(body) + "</svg>")


def _text(x: float, y: float, s: str, cls: str = "", anchor: str = "start",
          extra: str = "") -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" class="{cls}" text-anchor="{anchor}" {extra}>'
            f"{escape(s)}</text>")


def _ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    step = (hi - lo) / n
    for nice in (0.01, 0.02, 0.025, 0.05, 0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10):
        if nice >= step:
            step = nice
            break
    start = (lo // step + 1) * step if lo % step else lo
    out, v = [], start
    while v <= hi + 1e-9:
        out.append(round(v, 6))
        v += step
    return out


def interval_bars(rows: list[tuple[str, float, float, float, str]], label: str,
                  reference: float = 0.5, fmt=lambda v: f"{v:.0%}") -> str:
    """Horizontal points with interval whiskers, one row each:
    (row label, value, low, high, colors). ``colors`` like "WU" paints a
    two-tone marker."""
    width, row_h, left, right, top = 640, 26, 90, 30, 16
    height = top + row_h * len(rows) + 34
    lo = min(r[2] for r in rows + [("", reference, reference, reference, "")])
    hi = max(r[3] for r in rows + [("", reference, reference, reference, "")])
    pad = (hi - lo) * 0.08 or 0.01
    lo, hi = lo - pad, hi + pad

    def x(v: float) -> float:
        return left + (v - lo) / (hi - lo) * (width - left - right)

    body = []
    for t in _ticks(lo, hi):
        body.append(f'<line x1="{x(t):.1f}" x2="{x(t):.1f}" y1="{top - 6}" '
                    f'y2="{height - 28}" class="grid"/>')
        body.append(_text(x(t), height - 12, fmt(t), "tick", "middle"))
    body.append(f'<line x1="{x(reference):.1f}" x2="{x(reference):.1f}" y1="{top - 6}" '
                f'y2="{height - 28}" class="ref"/>')
    for i, (name, v, a, b, colors) in enumerate(rows):
        y = top + row_h * i + row_h / 2
        body.append(_text(left - 10, y + 4, name, "label", "end"))
        body.append(f'<line x1="{x(a):.1f}" x2="{x(b):.1f}" y1="{y:.1f}" y2="{y:.1f}" '
                    f'class="whisker"/>')
        parts = list(colors) or ["C"]
        r = 7
        tip = f"<title>{escape(name)}: {fmt(v)} (95% interval {fmt(a)}–{fmt(b)})</title>"
        if len(parts) == 1:
            body.append(f'<circle cx="{x(v):.1f}" cy="{y:.1f}" r="{r}" '
                        f'fill="{MANA[parts[0]]}" class="dot">{tip}</circle>')
        else:
            cx = x(v)
            body.append(f'<g class="dot">{tip}'
                        f'<path d="M{cx:.1f},{y - r:.1f} A{r},{r} 0 0 0 {cx:.1f},{y + r:.1f} Z" '
                        f'fill="{MANA[parts[0]]}"/>'
                        f'<path d="M{cx:.1f},{y - r:.1f} A{r},{r} 0 0 1 {cx:.1f},{y + r:.1f} Z" '
                        f'fill="{MANA[parts[1]]}"/>'
                        f'<circle cx="{cx:.1f}" cy="{y:.1f}" r="{r}" fill="none" '
                        f'class="outline"/></g>')
    return _svg(width, height, body, label)


def scatter(points: list[tuple[str, float, float, str]], label: str, x_label: str,
            y_label: str, callouts: set[str] = frozenset(), x_fmt=lambda v: f"{v:g}",
            y_fmt=lambda v: f"{v:.0%}", y_ref: float | None = 0.5) -> str:
    """(name, x, y, color key) points; names in ``callouts`` are labelled."""
    width, height, left, right, top, bottom = 640, 420, 56, 20, 16, 46
    xs = [p[1] for p in points]
    ys = [p[2] for p in points]
    x_lo, x_hi = min(xs), max(xs)
    y_lo, y_hi = min(ys), max(ys)
    xp, yp = (x_hi - x_lo) * 0.05 or 1, (y_hi - y_lo) * 0.06 or 0.01
    x_lo, x_hi, y_lo, y_hi = x_lo - xp, x_hi + xp, y_lo - yp, y_hi + yp

    def px(v: float) -> float:
        return left + (v - x_lo) / (x_hi - x_lo) * (width - left - right)

    def py(v: float) -> float:
        return top + (y_hi - v) / (y_hi - y_lo) * (height - top - bottom)

    body = []
    for t in _ticks(x_lo, x_hi, 7):
        body.append(f'<line x1="{px(t):.1f}" x2="{px(t):.1f}" y1="{top}" '
                    f'y2="{height - bottom}" class="grid"/>')
        body.append(_text(px(t), height - bottom + 16, x_fmt(t), "tick", "middle"))
    for t in _ticks(y_lo, y_hi, 6):
        body.append(f'<line x1="{left}" x2="{width - right}" y1="{py(t):.1f}" '
                    f'y2="{py(t):.1f}" class="grid"/>')
        body.append(_text(left - 6, py(t) + 4, y_fmt(t), "tick", "end"))
    if y_ref is not None and y_lo < y_ref < y_hi:
        body.append(f'<line x1="{left}" x2="{width - right}" y1="{py(y_ref):.1f}" '
                    f'y2="{py(y_ref):.1f}" class="ref"/>')
    body.append(_text((left + width - right) / 2, height - 8, x_label, "axis", "middle"))
    body.append(_text(14, (top + height - bottom) / 2, y_label, "axis", "middle",
                      f'transform="rotate(-90 14 {(top + height - bottom) / 2:.1f})"'))
    for name, x, y, key in sorted(points, key=lambda p: p[0] in callouts):
        tip = f"<title>{escape(name)}: {x_fmt(x)}, {y_fmt(y)}</title>"
        cls = "pt call" if name in callouts else "pt"
        body.append(f'<circle cx="{px(x):.1f}" cy="{py(y):.1f}" r="{5 if name in callouts else 3.6}"'
                    f' fill="{MANA[key]}" class="{cls}">{tip}</circle>')
    for name, x, y, _ in points:
        if name in callouts:
            anchor = "end" if px(x) > width * 0.7 else "start"
            dx = -8 if anchor == "end" else 8
            body.append(_text(px(x) + dx, py(y) + 4, name, "callout", anchor))
    return _svg(width, height, body, label)


def stacked_share(rows: list[tuple[str, dict[str, float]]], label: str,
                  order: str = "WUBRG") -> str:
    """One horizontal 100% bar per row, split by color share."""
    width, row_h, left, right, top = 640, 30, 90, 20, 10
    height = top + row_h * len(rows) + 30
    body = []
    for i, (name, shares) in enumerate(rows):
        y = top + row_h * i
        body.append(_text(left - 10, y + row_h / 2 + 4, name, "label", "end"))
        total = sum(shares.values()) or 1
        x = left
        for c in order:
            w = shares.get(c, 0) / total * (width - left - right)
            if w <= 0:
                continue
            tip = f"<title>{c}: {shares.get(c, 0) / total:.0%}</title>"
            body.append(f'<rect x="{x:.1f}" y="{y + 4}" width="{w:.1f}" height="{row_h - 8}" '
                        f'fill="{MANA[c]}" class="seg">{tip}</rect>')
            if w > 28:
                body.append(_text(x + w / 2, y + row_h / 2 + 4,
                                  f"{shares.get(c, 0) / total:.0%}", "seglabel", "middle"))
            x += w
    return _svg(width, height, body, label)


def bars(rows: list[tuple[str, float, str]], label: str, fmt=lambda v: f"{v:.1f}",
         zero: float = 0.0) -> str:
    """Simple horizontal bars: (label, value, color key), drawn from ``zero``."""
    width, row_h, left, right, top = 640, 22, 210, 50, 8
    height = top + row_h * len(rows) + 12
    lo = min([zero] + [r[1] for r in rows])
    hi = max([zero] + [r[1] for r in rows])

    def x(v: float) -> float:
        return left + (v - lo) / ((hi - lo) or 1) * (width - left - right)

    body = []
    for i, (name, v, key) in enumerate(rows):
        y = top + row_h * i
        a, b = sorted((x(zero), x(v)))
        body.append(_text(left - 8, y + row_h / 2 + 4, name, "label", "end"))
        body.append(f'<rect x="{a:.1f}" y="{y + 3}" width="{max(b - a, 1):.1f}" '
                    f'height="{row_h - 6}" fill="{MANA[key]}" class="seg">'
                    f"<title>{escape(name)}: {fmt(v)}</title></rect>")
        body.append(_text(b + 5 if v >= zero else a - 5, y + row_h / 2 + 4, fmt(v), "tick",
                          "start" if v >= zero else "end"))
    return _svg(width, height, body, label)
