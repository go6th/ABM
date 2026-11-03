"""Tiny SVG diagram kit: boxes, zones and arrows placed by hand-picked coordinates.

Used only to draw the figures of the project audit PDF (build.py). No third-party packages.
"""
from __future__ import annotations

from html import escape

KINDS = {
    # kind: (fill, stroke, text)
    "user": ("#ede9fe", "#7c6fd6", "#2b2257"),
    "web": ("#dbeafe", "#4a7fd4", "#12306b"),
    "api": ("#dcfce7", "#3f9d63", "#14532d"),
    "worker": ("#ffedd5", "#d98a3d", "#6b3205"),
    "data": ("#e5e7eb", "#6b7280", "#1f2937"),
    "ext": ("#fce7f3", "#d1639a", "#5e0f33"),
    "ai": ("#fef9c3", "#c9a60a", "#5b4a02"),
    "gate": ("#fee2e2", "#d65a5a", "#6b1212"),
    "plain": ("#ffffff", "#8a94a6", "#1f2937"),
    "done": ("#d1fae5", "#10b981", "#064e3b"),
}


class Diagram:
    def __init__(self, w: int, h: int, title: str = ""):
        self.w, self.h = w, h
        self.parts: list[str] = []
        self.boxes: dict[str, tuple[float, float, float, float]] = {}
        self.title = title

    # ---- shapes -------------------------------------------------------
    def zone(self, x, y, w, h, label="", dash=True):
        d = ' stroke-dasharray="6 4"' if dash else ""
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="#f8fafc" '
            f'stroke="#b6c0d0" stroke-width="1.2"{d}/>'
        )
        if label:
            self.parts.append(
                f'<text x="{x + 12}" y="{y + 18}" font-size="11.5" font-weight="700" '
                f'fill="#64748b" letter-spacing="0.6">{escape(label.upper())}</text>'
            )

    def box(self, bid, x, y, w, h, text, kind="plain", size=12, bold_first=True, shape="round"):
        fill, stroke, tc = KINDS[kind]
        self.boxes[bid] = (x, y, w, h)
        rx = 10 if shape == "round" else (h / 2 if shape == "pill" else 3)
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="1.5"/>'
        )
        lines = text.split("\n")
        lh = size + 3
        y0 = y + h / 2 - (len(lines) - 1) * lh / 2 + size * 0.35
        for i, ln in enumerate(lines):
            wt = "700" if (i == 0 and bold_first and len(lines) > 1) else "400"
            self.parts.append(
                f'<text x="{x + w / 2}" y="{y0 + i * lh}" font-size="{size}" text-anchor="middle" '
                f'fill="{tc}" font-weight="{wt}">{escape(ln)}</text>'
            )

    def note(self, x, y, text, size=11, color="#475569", anchor="start", bold=False):
        lines = text.split("\n")
        for i, ln in enumerate(lines):
            self.parts.append(
                f'<text x="{x}" y="{y + i * (size + 3)}" font-size="{size}" fill="{color}" '
                f'text-anchor="{anchor}" font-weight="{700 if bold else 400}">{escape(ln)}</text>'
            )

    # ---- arrows -------------------------------------------------------
    def _anchor(self, bid, side):
        x, y, w, h = self.boxes[bid]
        return {
            "r": (x + w, y + h / 2),
            "l": (x, y + h / 2),
            "t": (x + w / 2, y),
            "b": (x + w / 2, y + h),
        }[side]

    def arrow(self, a, b, sa="r", sb="l", label="", via=None, dashed=False, color="#475569",
              label_dx=0, label_dy=-5, both=False):
        """Arrow from box a (side sa) to box b (side sb); `via` = list of waypoints."""
        p0 = self._anchor(a, sa)
        p1 = self._anchor(b, sb)
        pts = [p0] + (via or []) + [p1]
        d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        da = ' stroke-dasharray="5 4"' if dashed else ""
        ms = ' marker-start="url(#arr)"' if both else ""
        self.parts.append(
            f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.5"{da} '
            f'marker-end="url(#arr)"{ms}/>'
        )
        if label:
            # label near the middle of the longest segment
            best = max(range(len(pts) - 1), key=lambda i: abs(pts[i + 1][0] - pts[i][0]) + abs(pts[i + 1][1] - pts[i][1]))
            mx = (pts[best][0] + pts[best + 1][0]) / 2 + label_dx
            my = (pts[best][1] + pts[best + 1][1]) / 2 + label_dy
            lines = label.split("\n")
            for i, ln in enumerate(lines):
                self.parts.append(
                    f'<text x="{mx}" y="{my + i * 12}" font-size="10.5" text-anchor="middle" '
                    f'fill="#334155" paint-order="stroke" stroke="#ffffff" stroke-width="3">{escape(ln)}</text>'
                )

    def line(self, pts, dashed=False, color="#475569", arrow=True, label="", lx=0, ly=0):
        d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        da = ' stroke-dasharray="5 4"' if dashed else ""
        me = ' marker-end="url(#arr)"' if arrow else ""
        self.parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.5"{da}{me}/>')
        if label:
            self.parts.append(
                f'<text x="{lx}" y="{ly}" font-size="10.5" text-anchor="middle" fill="#334155" '
                f'paint-order="stroke" stroke="#ffffff" stroke-width="3">{escape(label)}</text>'
            )

    # ---- output -------------------------------------------------------
    def svg(self) -> str:
        head = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" '
            f'width="100%" font-family="Helvetica, Arial, DejaVu Sans, sans-serif" role="img" '
            f'aria-label="{escape(self.title)}">'
            '<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
            'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#475569"/>'
            "</marker></defs>"
        )
        return head + "".join(self.parts) + "</svg>"
