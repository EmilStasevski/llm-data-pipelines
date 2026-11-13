"""Report figures beyond the README bars (PLAN §13): Pareto scatter time vs. energy per track,
Track D quality vs. energy, Track E stage breakdown and the Phase 9 load curve. Plain SVG (same palette and
chrome as readme.py), written to reports/figures/ by `task report`; every number comes from
results/zj.duckdb (rule 3).

Marks carry a <title> (hover tooltip in a browser); identity never by color alone (legend + direct labels).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from zj_analysis.readme import esc, nice, quality_d, tick_label, ticks_log
from zj_analysis.report import (
    DETAIL_SQL,
    PRODUCTION_STACKS,
    accumulate,
    fmt_num,
    median_of,
    section_filter,
    summaries,
)

# categorical slots 1–5 of the reference palette (light / dark steps), fixed order
SLOTS = [
    ("#2a78d6", "#3987e5"),
    ("#eb6834", "#d95926"),
    ("#1baf7a", "#199e70"),
    ("#eda100", "#c98500"),
    ("#e87ba4", "#d55181"),
]
CSS = (
    ".bg{fill:#fcfcfb}.ink{fill:#0b0b0b}.sec{fill:#52514e}.mut{fill:#898781}.grid{stroke:#e1e0d9}"
    ".axis{stroke:#c3c2b7}.dom{fill:#c3c2b7}.ref{stroke:#898781;stroke-dasharray:4 4}"
    + "".join(f".c{i}{{fill:{lt}}}.l{i}{{stroke:{lt}}}" for i, (lt, _) in enumerate(SLOTS))
    + "@media (prefers-color-scheme:dark){.bg{fill:#1a1a19}.ink{fill:#f0efec}.sec{fill:#c3c2b7}"
    ".grid{stroke:#2c2c2a}.axis{stroke:#383835}.dom{fill:#52514e}"
    + "".join(f".c{i}{{fill:{dk}}}.l{i}{{stroke:{dk}}}" for i, (_, dk) in enumerate(SLOTS))
    + '}text{font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;font-size:12px}'
    ".mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}"
)

TXT = {
    "de": {
        "title": "## Abbildungen",
        "intro": "Generiert aus `results/` (Median der neuesten Sitzung je Wettbewerber und Skala). "
        "Pareto-Front (blau, nummeriert, Namen unter dem Titel): kein anderer Wettbewerber ist auf beiden "
        "Achsen besser. Anti-Muster und Produktions-Stacks sind nicht eingezeichnet.",
        "pareto": "{track}: Wall-Zeit vs. Energie (brutto), Skala {scale}",
        "wall": "Wall-Zeit s (log)",
        "energy": "Energie J brutto (log)",
        "d": "Track D: Qualität vs. Energie je Zeile, Skala {scale}",
        "jrow": "J/Zeile brutto (log)",
        "f1": "macro-F1",
        "stages": "Track E: Zeit je Stufe, Skala {scale} (s)",
        "load": "Lastkurve {c}, Skala {scale}: p95-Latenz je Angebotsrate",
        "rate": "Angebot req/s (log)",
        "p95": "p95 ms (log)",
        "s_search": "/v1/search p95",
        "s_first": "/v1/ask erstes Ereignis p95",
        "s_ask": "/v1/ask gesamt p95",
        "slo": "SLO",
    },
    "en": {
        "title": "## Figures",
        "intro": "Generated from `results/` (median of the latest session per contestant and scale). "
        "Pareto front (blue, numbered, names under the title): no other contestant is better on both axes. "
        "Anti-patterns and production stacks are not plotted.",
        "pareto": "{track}: wall time vs. energy (gross), scale {scale}",
        "wall": "wall time s (log)",
        "energy": "energy J gross (log)",
        "d": "Track D: quality vs. energy per row, scale {scale}",
        "jrow": "J/row gross (log)",
        "f1": "macro-F1",
        "stages": "Track E: time per stage, scale {scale} (s)",
        "load": "Load curve {c}, scale {scale}: p95 latency per offered rate",
        "rate": "offered req/s (log)",
        "p95": "p95 ms (log)",
        "s_search": "/v1/search p95",
        "s_first": "/v1/ask first event p95",
        "s_ask": "/v1/ask total p95",
        "slo": "SLO",
    },
}
PARETO = [
    ("A", "L", "Track A"),
    ("B", "M", "Track B"),
    ("C", "M", "Track C dense"),
    ("C-hybrid", "M", "Track C hybrid"),
    ("E", "L", "Track E"),
]
W, H = 820, 440
L, R, T, B = 80, 30, 58, 50  # plot margins


@dataclass(frozen=True)
class Pt:
    label: str
    x: float
    y: float
    tip: str


def _head(title: str, h: int = H) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {h}" width="{W}" height="{h}" role="img" '
        f'aria-label="{esc(title)}"><style>{CSS}</style>',
        f'<rect class="bg" width="{W}" height="{h}" rx="6"/>',
        f'<text class="ink" x="16" y="22" font-weight="600">{esc(title)}</text>',
    ]


def _scale(lo: float, hi: float, a: float, b: float, *, log: bool) -> Callable[[float], float]:
    if log:
        return lambda v: a + (b - a) * (math.log10(v) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))
    return lambda v: a + (b - a) * (v - lo) / (hi - lo)


def lin_ticks(lo: float, hi: float) -> list[float]:
    step = 10 ** math.floor(math.log10((hi - lo) / 4))
    step = next(m * step for m in (1, 2, 5, 10) if (hi - lo) / (m * step) <= 6)
    first = math.ceil(lo / step - 1e-9) * step
    return [round(first + i * step, 10) for i in range(int((hi - first) / step + 1e-9) + 1)]


def _axes(  # noqa: PLR0913
    xs: Sequence[float],
    ys: Sequence[float],
    xlab: str,
    ylab: str,
    *,
    logx: bool,
    logy: bool,
) -> tuple[list[str], Callable[[float], float], Callable[[float], float]]:
    def rng(v: Sequence[float], log: bool) -> tuple[float, float]:
        if log:
            return nice(min(v), down=True), nice(max(v), down=False)
        span = (max(v) - min(v)) or 1.0
        return min(v) - 0.05 * span, max(v) + 0.05 * span

    (x0, x1), (y0, y1) = rng(xs, logx), rng(ys, logy)
    fx, fy = _scale(x0, x1, L, W - R, log=logx), _scale(y0, y1, H - B, T, log=logy)
    out: list[str] = []
    for t in ticks_log(x0, x1) if logx else lin_ticks(x0, x1):
        out += [
            f'<line class="grid" x1="{fx(t):.1f}" x2="{fx(t):.1f}" y1="{T}" y2="{H - B}"/>',
            f'<text class="mut" x="{fx(t):.1f}" y="{H - B + 16}" text-anchor="middle">{tick_label(t)}</text>',
        ]
    for t in ticks_log(y0, y1) if logy else lin_ticks(y0, y1):
        out += [
            f'<line class="grid" x1="{L}" x2="{W - R}" y1="{fy(t):.1f}" y2="{fy(t):.1f}"/>',
            f'<text class="mut" x="{L - 6}" y="{fy(t) + 4:.1f}" text-anchor="end">{tick_label(t)}</text>',
        ]
    out += [
        f'<line class="axis" x1="{L}" x2="{W - R}" y1="{H - B}" y2="{H - B}"/>',
        f'<line class="axis" x1="{L}" x2="{L}" y1="{T}" y2="{H - B}"/>',
        f'<text class="sec" x="{(L + W - R) / 2}" y="{H - 12}" text-anchor="middle">{esc(xlab)}</text>',
        f'<text class="sec" x="16" y="{(T + H - B) / 2}" transform="rotate(-90 16 {(T + H - B) / 2})" '
        f'text-anchor="middle">{esc(ylab)}</text>',
    ]
    return out, fx, fy


def front(pts: Sequence[Pt], *, maximize_y: bool = False) -> set[str]:
    """Labels of the Pareto-optimal points (x smaller is better; y smaller, or larger with maximize_y)."""

    def better(a: Pt, b: Pt) -> bool:  # a dominates b
        ya, yb = (-a.y, -b.y) if maximize_y else (a.y, b.y)
        return a.x <= b.x and ya <= yb and (a.x < b.x or ya < yb)

    return {p.label for p in pts if not any(better(q, p) for q in pts if q is not p)}


def svg_scatter(  # noqa: PLR0913
    title: str, pts: Sequence[Pt], xlab: str, ylab: str, *, logy: bool = True, maximize_y: bool = False
) -> str:
    out = _head(title)
    ax, fx, fy = _axes([p.x for p in pts], [p.y for p in pts], xlab, ylab, logx=True, logy=logy)
    out += ax
    best = front(pts, maximize_y=maximize_y)
    num = {
        p.label: k for k, p in enumerate(sorted((p for p in pts if p.label in best), key=lambda p: p.x), 1)
    }
    key = "  ·  ".join(f"{k} {lab}" for lab, k in sorted(num.items(), key=lambda kv: kv[1]))
    out.append(f'<text class="sec mono" x="16" y="42" font-size="11">{esc(key)}</text>')
    for p in sorted(pts, key=lambda p: p.label in best):  # front drawn last, on top
        on = p.label in best
        out.append(
            f'<circle class="{"c0" if on else "dom"}" cx="{fx(p.x):.1f}" cy="{fy(p.y):.1f}" '
            f'r="{5 if on else 4}"><title>{esc(p.tip)}</title></circle>'
        )
        if on:  # number beside the point, names in the key line under the title
            out.append(
                f'<text class="ink" x="{fx(p.x) + 8:.1f}" y="{fy(p.y) - 6:.1f}" font-weight="600" '
                f'font-size="11">{num[p.label]}</text>'
            )
    out.append("</svg>")
    return "\n".join(out) + "\n"


def svg_stacked(
    title: str, labels: Sequence[str], stages: Sequence[str], vals: Sequence[Sequence[float]], lang: str
) -> str:
    """Horizontal stacked bars (one row per contestant, one segment per stage, 2 px surface gaps)."""
    left, right, top, row = 200, 90, 62, 30
    h = top + row * len(labels) + 34
    hi = max(sum(v) for v in vals) * 1.1
    fx = _scale(0, hi, left, W - right, log=False)
    out = _head(title, h)
    x = 16.0
    for i, s in enumerate(stages):  # legend, fixed slot order
        out.append(
            f'<rect class="c{i}" x="{x}" y="34" width="10" height="10" rx="2"/>'
            f'<text class="sec" x="{x + 15}" y="43">{esc(s)}</text>'
        )
        x += 30 + 7 * len(s)
    yb = top + row * len(labels)
    for t in lin_ticks(0, hi):
        out += [
            f'<line class="grid" x1="{fx(t):.1f}" x2="{fx(t):.1f}" y1="{top - 4}" y2="{yb}"/>',
            f'<text class="mut" x="{fx(t):.1f}" y="{yb + 16}" text-anchor="middle">{tick_label(t)}</text>',
        ]
    for j, (lab, v) in enumerate(zip(labels, vals, strict=True)):
        y = top + j * row + 6
        out.append(f'<text class="sec mono" x="{left - 8}" y="{y + 13}" text-anchor="end">{esc(lab)}</text>')
        acc = 0.0
        for i, (s, t) in enumerate(zip(stages, v, strict=True)):
            if t <= 0:
                continue
            x0, x1 = fx(acc), fx(acc + t)
            out.append(
                f'<rect class="c{i}" x="{x0:.1f}" y="{y}" width="{max(x1 - x0 - 2, 1):.1f}" '
                f'height="18" rx="2">'
                f"<title>{esc(lab)} · {esc(s)}: {fmt_num(t, 0, lang)} s</title></rect>"
            )
            acc += t
        out.append(f'<text class="ink" x="{fx(acc) + 6:.1f}" y="{y + 13}">{fmt_num(acc, 0, lang)} s</text>')
    out += [f'<line class="axis" x1="{left}" x2="{left}" y1="{top - 4}" y2="{yb}"/>', "</svg>"]
    return "\n".join(out) + "\n"


def svg_lines(
    title: str,
    series: Sequence[tuple[str, Sequence[tuple[float, float]]]],
    refs: Sequence[tuple[float, str]],
    xlab: str,
    ylab: str,
) -> str:
    """Log-log lines with markers; legend on top, direct label at each line's end, dashed reference lines."""
    xs = [x for _, s in series for x, _ in s]
    ys = [y for _, s in series for _, y in s] + [r for r, _ in refs]
    out = _head(title)
    ax, fx, fy = _axes(xs, ys, xlab, ylab, logx=True, logy=True)
    out += ax
    for r, lab in refs:
        out += [
            f'<line class="ref" x1="{L}" x2="{W - R}" y1="{fy(r):.1f}" y2="{fy(r):.1f}"/>',
            f'<text class="mut" x="{W - R - 4}" y="{fy(r) - 4:.1f}" text-anchor="end">{esc(lab)}</text>',
        ]
    x = 16.0
    for i, (name, pts) in enumerate(series):
        out.append(
            f'<rect class="c{i}" x="{x}" y="30" width="10" height="10" rx="2"/>'
            f'<text class="sec" x="{x + 15}" y="39">{esc(name)}</text>'
        )
        x += 30 + 7 * len(name)
        d = " ".join(f"{'M' if k == 0 else 'L'}{fx(a):.1f},{fy(b):.1f}" for k, (a, b) in enumerate(pts))
        out.append(f'<path class="l{i}" d="{d}" fill="none" stroke-width="2"/>')
        for a, b in pts:
            out.append(
                f'<circle class="c{i}" cx="{fx(a):.1f}" cy="{fy(b):.1f}" r="4"><title>{esc(name)}: '
                f"{tick_label(a)} req/s → {b:,.0f} ms</title></circle>"
            )
    out.append("</svg>")
    return "\n".join(out) + "\n"


def _pareto_pts(con: duckdb.DuckDBPyConnection, section: str, scale: str, lang: str) -> list[Pt]:
    return [
        Pt(
            s.contestant,
            s.wall,
            s.e_gross,
            f"{s.contestant}: {fmt_num(s.wall, 1, lang)} s · {fmt_num(s.e_gross, 0, lang)} J",
        )
        for s in summaries(con, section)
        if s.scale == scale and s.e_gross and not s.anti and s.contestant not in PRODUCTION_STACKS
    ]


def _stage_medians(con: duckdb.DuckDBPyConnection, section: str, scale: str) -> dict[str, dict[str, float]]:
    acc: dict[str, dict[str, list[float]]] = {}
    for c, sc, st, ex, q, rows in con.execute(DETAIL_SQL, list(section_filter(section))).fetchall():
        if sc == scale:
            accumulate(acc.setdefault(str(c), {}), st, ex, q, rows)
    return {
        c: {k[2:]: m for k, v in d.items() if k.startswith("t_") and (m := median_of(v)) is not None}
        for c, d in acc.items()
    }


def section_md(con: duckdb.DuckDBPyConnection, figs: Path, lang: str) -> str:
    t = TXT[lang]
    figs.mkdir(parents=True, exist_ok=True)
    out = [t["title"], "", t["intro"], ""]

    def emit(stem: str, title: str, svg: str) -> None:
        (figs / f"{stem}.{lang}.svg").write_text(svg, encoding="utf-8")
        out.extend([f"![{esc(title)}](figures/{stem}.{lang}.svg)", ""])

    for section, scale, name in PARETO:
        if len(pts := _pareto_pts(con, section, scale, lang)) >= 3:
            title = t["pareto"].format(track=name, scale=scale)
            emit(
                f"pareto_{section.lower().replace('-', '_')}_{scale}",
                title,
                svg_scatter(title, pts, t["wall"], t["energy"]),
            )
    d_pts: list[Pt] = []
    for s in summaries(con, "D"):
        if s.scale == "D2k" and s.e_gross and s.rows:
            f1 = quality_d(con, "D2k").get(s.contestant, (None, None))[0]
            if f1 is not None:
                d_pts.append(
                    Pt(
                        s.contestant,
                        s.e_gross / s.rows,
                        f1,
                        f"{s.contestant}: {fmt_num(s.e_gross / s.rows, 2, lang)} J/row · "
                        f"F1 {fmt_num(f1, 3, lang)}",
                    )
                )
    if len(d_pts) >= 3:
        title = t["d"].format(scale="D2k")
        emit(
            "track_d_f1_energy_D2k",
            title,
            svg_scatter(title, d_pts, t["jrow"], t["f1"], logy=False, maximize_y=True),
        )
    has_e = any(s.scale == "L" for s in summaries(con, "E"))
    if has_e and (stg := _stage_medians(con, "E", "L")):
        order = [c for c in sorted(stg, key=lambda c: sum(stg[c].values())) if c not in PRODUCTION_STACKS]
        names = list(dict.fromkeys(k for c in order for k in stg[c] if k != "write"))[: len(SLOTS)]
        if order and names:
            title = t["stages"].format(scale="L")
            emit(
                "stages_e_L",
                title,
                svg_stacked(title, order, names, [[stg[c].get(k, 0.0) for k in names] for c in order], lang),
            )
    from zj_analysis import load_curve  # noqa: PLC0415  (load_curve imports report, which imports this lazily)

    n_load: Any = con.execute("SELECT count(*) FROM ok_runs WHERE task = 'e.load'").fetchone()
    for cv in load_curve.curves(con) if n_load and n_load[0] else []:

        def pick(
            ep: str, f: Callable[[load_curve.Step], float | None], steps: list[load_curve.Step] = cv.steps
        ) -> list[tuple[float, float]]:
            return [(s.rate, v) for s in steps if s.endpoint == ep and (v := f(s)) is not None and v > 0]

        series = [
            (t["s_search"], pick("search", lambda s: s.p95)),
            (t["s_first"], pick("ask", lambda s: s.first_p95)),
            (t["s_ask"], pick("ask", lambda s: s.p95)),
        ]
        series = [(n, p) for n, p in series if p]
        if series:
            title = t["load"].format(c=cv.contestant, scale=cv.scale)
            refs = [
                (load_curve.SEARCH_SLO_P95_MS, f"{t['slo']} /v1/search {load_curve.SEARCH_SLO_P95_MS:g} ms"),
                (
                    load_curve.ASK_SLO_FIRST_P95_MS,
                    f"{t['slo']} {t['s_first']} {load_curve.ASK_SLO_FIRST_P95_MS:g} ms",
                ),
            ]
            emit(
                f"load_curve_{cv.contestant}_{cv.scale}",
                title,
                svg_lines(title, series, refs, t["rate"], t["p95"]),
            )
    return "\n".join(out)
