#!/usr/bin/env python3
"""Render the animated SVG cards for the IvzCyberSec profile README.

    python render_cards.py              # animated cards -> cards/
    STATIC=1 python render_cards.py     # final frame only (for screenshots)
    DATA=path/to.json python render_cards.py

Reads the GitHub GraphQL response that .github/workflows/profile-cards.yml
saves to data/github.json every day. The cards are shown through <img>, so
each one is fully self-contained: one inline <style> (CSS keyframes), SMIL
<animate> elements, and fonts subset with pyftsubset and embedded as base64
WOFF data URIs. No scripts, no external requests.
"""
from __future__ import annotations

import base64
import datetime as dt
import html
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from collections import OrderedDict
from pathlib import Path

from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "cards"
DATA = Path(os.environ.get("DATA", ROOT / "data" / "github.json"))
STATIC = os.environ.get("STATIC", "") == "1"

# --------------------------------------------------------------------------
# Theme. Swap these for the exact values from ivancyber.uk.
# --------------------------------------------------------------------------
THEME = {
    "bg": "#0b0d10",      # card background (near-black, like the old #111111 badges)
    "panel": "#12161b",   # inner panels, pills
    "line": "#20272f",    # borders and gridlines
    "text": "#e8edf2",
    "muted": "#8a96a3",
    "faint": "#56606b",
    "accent": "#3ddc97",  # brand colour (bars, graph, highlights)
    "empty": "#151a20",   # zero-contribution cells
}
FONTS = {  # key: (file, weight, CSS fallback stack)
    "display": ("fonts/InterDisplay-Bold.otf", 700, "Inter,'Segoe UI',system-ui,sans-serif"),
    "sans": ("fonts/Inter-Regular.otf", 400, "Inter,'Segoe UI',system-ui,sans-serif"),
    "sansb": ("fonts/Inter-SemiBold.otf", 600, "Inter,'Segoe UI',system-ui,sans-serif"),
    "mono": ("fonts/DejaVuSansMono.ttf", 400, "ui-monospace,Menlo,Consolas,monospace"),
    "monob": ("fonts/DejaVuSansMono-Bold.ttf", 700, "ui-monospace,Menlo,Consolas,monospace"),
}

# --------------------------------------------------------------------------
# Content
# --------------------------------------------------------------------------
PROFILE = {
    "login": "IvzCyberSec",
    "name": "Ivan",
    "about": "Final-year Cyber Security student at BCU: offensive security and toolsmithing.",
    "meta": ["Birmingham, UK", "Head of Cyber Security @ BCUCompSoc", "EN / RU / IT"],
    "prompt": "ivan@ivancyber:~$ ",
    "command": "whoami",
}
# Optional portrait: first of these that exists is embedded in the hero.
PORTRAIT_CANDIDATES = ["assets/portrait.png", "assets/portrait.jpg", "assets/portrait.jpeg"]

TILES = [
    {"file": "tile-portfolio", "label": "Portfolio", "title": "ivancyber.uk",
     "sub": "Projects, skills and experience", "url": "https://ivancyber.uk"},
    {"file": "tile-workshops", "label": "Workshops", "title": "Workshop library",
     "sub": "BCUCompSoc cyber workshops", "url": "https://workshop.ivancyber.uk"},
    {"file": "tile-linkedin", "label": "LinkedIn", "title": "Connect",
     "sub": "Experience and contact", "url": "https://linkedin.com/in/ivan-k-036007387"},
]

# Your logo mark from ivancyber.uk (viewBox 290 110 750 495).
LOGO = ["442,260 595,140 553,268 647,378 598,590 303,590 490,330",
        "617,240 779,125 779,345 836,362 798,492 770,398 672,380",
        "877,310 962,376 937,414 1030,548 1030,560 876,560 847,442 900,385"]

ROLE = {
    "title": "Head of Cyber Security",
    "org": "BCU Computer Society (BCUCompSoc)",
    "dates": "Aug 2026 - present",
    "summary": "I design and run weekly hands-on cyber workshops, from first idea to working project.",
    "stats": [("20-40", "students per session"), ("1", "session from zero to GitHub"),
              ("CTF", "challenges and red-team labs")],
    "steps": ["Build it from scratch", "Push to GitHub", "Add to portfolio"],
    "url": "https://workshop.ivancyber.uk",
    "link": "workshop.ivancyber.uk",
}

FEATURED = [
    {"repo": "SYNACK-Scan-Your-Network-ACKnowledged", "title": "SYNACK",
     "subtitle": "Scan Your Network, ACKnowledged",
     "blurb": "A terminal-based reconnaissance and assessment platform built around Nmap: "
              "reusable scan profiles, NSE and service analysis, CVE intelligence, "
              "multi-host workspaces and JSON, CSV, XML and HTML reports.",
     "tags": ["Python", "Nmap", "Security Automation", "CVE Intelligence"]},
    {"repo": "SYNACK-Home-Lab", "title": "SYNACK Home Lab",
     "subtitle": "Compact physical networking and security lab",
     "blurb": "A custom rack combining a TP-Link N300 router, D-Link DGS-108 switch and "
              "Raspberry Pi 4, with CAD-adapted, 3D-printed enclosures. Built for Nmap "
              "enumeration, Wireshark analysis and student CTF demos.",
     "tags": ["Networking", "Hardware", "3D Printing", "Infrastructure"]},
    {"repo": "BCU-Welcome-Week-Project-2026-Cyber-Security", "title": "BCU Welcome Week",
     "subtitle": "Cyber Security project, 2026",
     "blurb": "Documentation for a hands-on cybersecurity Welcome Week project at Birmingham "
              "City University, turning technical concepts into activities new students "
              "can explore.",
     "tags": ["Education", "Workshop Development", "Documentation"]},
    {"repo": "LinuxFundamentals", "title": "Linux Fundamentals",
     "subtitle": "OverTheWire Bandit and the command line",
     "blurb": "A structured record of practical Linux learning: enumeration, permissions, "
              "SSH, text filtering, pipelines, Base64 decoding and binary inspection.",
     "tags": ["Linux", "SSH", "Command Line", "Problem Solving"]},
]

# Page order -> start time (s). Every card's local delays add on to these, so
# the README plays top to bottom.
T_HERO = 0.0
T_TILES = [0.55, 0.7, 0.85]
T_ROLE = 1.1
T_CHART = 1.9
T_REPOS = [2.7 + 0.3 * i for i in range(len(FEATURED))]
T_GRAPH = 2.7 + 0.3 * len(FEATURED) + 0.2

W = 840  # card width in user units; README shows it at 100%


# --------------------------------------------------------------------------
# Fonts: measuring and subsetting
# --------------------------------------------------------------------------
_metrics: dict[str, tuple] = {}


def _font(key):
    if key not in _metrics:
        f = TTFont(ROOT / FONTS[key][0], lazy=True)
        _metrics[key] = (f.getBestCmap(), f["hmtx"], f["head"].unitsPerEm)
    return _metrics[key]


def text_width(s: str, key: str, size: float) -> float:
    cmap, hmtx, upm = _font(key)
    total = 0
    for ch in s:
        g = cmap.get(ord(ch), ".notdef")
        total += hmtx[g][0]
    return total * size / upm


def wrap(s: str, key: str, size: float, max_w: float) -> list[str]:
    lines, cur = [], ""
    for word in s.split():
        trial = f"{cur} {word}".strip()
        if cur and text_width(trial, key, size) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def _pyftsubset_cmd():
    exe = shutil.which("pyftsubset")
    return [exe] if exe else [sys.executable, "-m", "fontTools.subset"]


_subset_cache: dict[tuple, str] = {}


def font_face(key: str, chars: set[str]) -> str:
    path, weight, _ = FONTS[key]
    text = "".join(sorted(chars | {" "}))
    cache_key = (key, text)
    if cache_key not in _subset_cache:
        with tempfile.TemporaryDirectory() as td:
            tf, of = Path(td) / "text.txt", Path(td) / "out.woff"
            tf.write_text(text, encoding="utf-8")
            subprocess.run(
                _pyftsubset_cmd() + [
                    str(ROOT / path), f"--text-file={tf}", f"--output-file={of}",
                    "--flavor=woff", "--layout-features=kern,liga", "--no-hinting",
                    "--desubroutinize", "--name-IDs=1,2", "--notdef-outline", "--drop-tables+=FFTM",
                ],
                check=True,
            )
            _subset_cache[cache_key] = base64.b64encode(of.read_bytes()).decode()
    b64 = _subset_cache[cache_key]
    return (f"@font-face{{font-family:'c-{key}';font-weight:{weight};"
            f"src:url(data:font/woff;base64,{b64}) format('woff')}}")


# --------------------------------------------------------------------------
# Card builder
# --------------------------------------------------------------------------
KEYFRAMES = """
@keyframes rise{from{opacity:0;transform:translateY(12px)}}
@keyframes fade{from{opacity:0}}
@keyframes grow{from{transform:scaleY(0)}}
@keyframes pop{from{opacity:0;transform:scale(.3)}}
@keyframes wipe{from{transform:scaleX(0)}}
@keyframes slide{from{opacity:0;transform:translateX(-14px)}}
.a-rise{animation:rise .7s cubic-bezier(.2,.7,.2,1) both}
.a-fade{animation:fade .6s ease-out both}
.a-slide{animation:slide .6s cubic-bezier(.2,.7,.2,1) both}
.a-grow{animation:grow .9s cubic-bezier(.2,.7,.2,1) both;transform-box:fill-box;transform-origin:50% 100%}
.a-pop{animation:pop .4s cubic-bezier(.3,1.5,.5,1) both;transform-box:fill-box;transform-origin:center}
.a-wipe{animation:wipe .9s cubic-bezier(.6,0,.2,1) both;transform-box:fill-box;transform-origin:0 50%}
.a-type{animation:fade .01s linear both}
@media (prefers-reduced-motion:reduce){
*{animation:none!important}
.s-draw{stroke-dashoffset:0!important}
.s-show{opacity:1!important}
}
"""


def esc(s) -> str:
    return html.escape(str(s), quote=True)


class Card:
    def __init__(self, name: str, w: float, h: float, t0: float, title: str):
        self.name, self.w, self.h, self.t0, self.title = name, w, h, t0, title
        self.defs: list[str] = []
        self.body: list[str] = []
        self.chars: dict[str, set[str]] = {}

    # -- animation helpers -------------------------------------------------
    def a(self, kind: str, at: float) -> str:
        """CSS-animation attributes, or nothing in STATIC mode."""
        if STATIC:
            return ""
        return f' class="a-{kind}" style="animation-delay:{self.t0 + at:.2f}s"'

    def draw(self, length: float, at: float, dur: float = 0.8) -> tuple[str, str]:
        """SMIL stroke draw-on. Returns (attributes, child <animate>)."""
        if STATIC:
            return "", ""
        L = f"{length:.1f}"
        attrs = f' class="s-draw" stroke-dasharray="{L}" stroke-dashoffset="{L}"'
        anim = (f'<animate attributeName="stroke-dashoffset" from="{L}" to="0" '
                f'begin="{self.t0 + at:.2f}s" dur="{dur}s" fill="freeze" calcMode="spline" '
                f'keyTimes="0;1" keySplines=".6 0 .2 1"/>')
        return attrs, anim

    def appear(self, at: float, dur: float = 0.4) -> tuple[str, str]:
        """SMIL fade-in. Returns (attributes, child <animate>)."""
        if STATIC:
            return "", ""
        return (' class="s-show" opacity="0"',
                f'<animate attributeName="opacity" from="0" to="1" begin="{self.t0 + at:.2f}s" '
                f'dur="{dur}s" fill="freeze"/>')

    def blink(self, at: float) -> str:
        if STATIC:
            return ""
        return (f'<animate attributeName="opacity" values="1;1;0;0" keyTimes="0;.5;.5;1" '
                f'dur="1.1s" begin="{self.t0 + at:.2f}s" repeatCount="indefinite"/>')

    # -- primitives --------------------------------------------------------
    def add(self, s: str):
        self.body.append(s)

    def text(self, x, y, s, key="sans", size=14, fill=None, anchor="start",
             anim="", extra="", ls=None) -> str:
        self.chars.setdefault(key, set()).update(str(s))
        fam = f"'c-{key}',{FONTS[key][2]}"
        lsp = f' letter-spacing="{ls}"' if ls is not None else ""
        return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{esc(fam)}" '
                f'font-weight="{FONTS[key][1]}" font-size="{size}" fill="{fill or THEME["text"]}" '
                f'text-anchor="{anchor}"{lsp}{extra}{anim}>{esc(s)}</text>')

    def frame(self, rx=16):
        self.add(f'<rect x=".5" y=".5" width="{self.w - 1}" height="{self.h - 1}" rx="{rx}" '
                 f'fill="{THEME["bg"]}" stroke="{THEME["line"]}"/>')

    def svg(self) -> str:
        faces = "".join(font_face(k, v) for k, v in self.chars.items())
        css = faces + ("" if STATIC else KEYFRAMES)
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
            f'viewBox="0 0 {self.w} {self.h}" role="img" aria-labelledby="title">'
            f'<title id="title">{esc(self.title)}</title>'
            f'<style>{css}</style>'
            f'<defs>{"".join(self.defs)}</defs>'
            + "".join(self.body) + "</svg>\n"
        )

    def write(self):
        OUT.mkdir(exist_ok=True)
        path = OUT / f"{self.name}.svg"
        path.write_text(self.svg(), encoding="utf-8")
        print(f"  {path.relative_to(ROOT)}  {path.stat().st_size / 1024:.0f} KB")


def arrow_icon(c: Card, x, y, size, at, color):
    d = f"M{x} {y + size} L{x + size} {y} M{x + size * .3} {y} H{x + size} V{y + size * .7}"
    attrs, anim = c.draw(size * 3, at, 0.5)
    c.add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2.2" '
          f'stroke-linecap="round" stroke-linejoin="round"{attrs}>{anim}</path>')


def star_path(cx, cy, r):
    pts = []
    for i in range(10):
        rad = r if i % 2 == 0 else r * 0.45
        ang = -math.pi / 2 + i * math.pi / 5
        pts.append(f"{cx + rad * math.cos(ang):.1f},{cy + rad * math.sin(ang):.1f}")
    return "M" + " L".join(pts) + "Z"


def mix(hex1: str, hex2: str, t: float) -> str:
    a = [int(hex1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(hex2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------
def load_data():
    if not DATA.exists():
        sys.exit(f"No data at {DATA}. Run the 'Profile cards' workflow and pull, "
                 "or set DATA=path/to/github.json.")
    raw = json.loads(DATA.read_text(encoding="utf-8"))
    if raw.get("errors"):
        sys.exit(f"GraphQL errors in {DATA}: {raw['errors']}")
    user = raw["data"]["user"]
    days = [d for w in user["contributionsCollection"]["contributionCalendar"]["weeks"]
            for d in w["contributionDays"]]
    days.sort(key=lambda d: d["date"])
    repos = {r["name"].lower(): r for r in user["repositories"]["nodes"]}
    return user, days, repos


def stats(days):
    counts = [d["contributionCount"] for d in days]
    # Current streak: today may still be in progress, so a zero today doesn't break it.
    i = len(days) - 1
    if i >= 0 and counts[i] == 0:
        i -= 1
    current = 0
    while i >= 0 and counts[i] > 0:
        current += 1
        i -= 1
    longest = run = 0
    for c in counts:
        run = run + 1 if c else 0
        longest = max(longest, run)
    busiest = max(days, key=lambda d: (d["contributionCount"], d["date"])) if days else None
    active = sum(1 for c in counts if c)
    # Last 12 calendar months, ending with the month of the latest day.
    months: OrderedDict[str, int] = OrderedDict()
    if days:
        last = dt.date.fromisoformat(days[-1]["date"])
        y, m = last.year, last.month
        keys = []
        for _ in range(12):
            keys.append(f"{y:04d}-{m:02d}")
            y, m = (y, m - 1) if m > 1 else (y - 1, 12)
        for k in reversed(keys):
            months[k] = 0
        for d in days:
            k = d["date"][:7]
            if k in months:
                months[k] += d["contributionCount"]
    return {"total": sum(counts), "current": current, "longest": longest,
            "active": active, "span": len(days), "busiest": busiest, "months": months}


def fmt_date(iso: str, weekday=True) -> str:
    d = dt.date.fromisoformat(iso[:10])
    return d.strftime(("%a " if weekday else "") + "%-d %b %Y")


def ago(iso: str | None) -> str:
    if not iso:
        return ""
    then = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    days = (dt.datetime.now(dt.timezone.utc) - then).days
    if days <= 0:
        return "updated today"
    if days == 1:
        return "updated yesterday"
    if days < 30:
        return f"updated {days} days ago"
    if days < 365:
        n = days // 30
        return f"updated {n} month{'s' if n > 1 else ''} ago"
    n = days // 365
    return f"updated {n} year{'s' if n > 1 else ''} ago"


# --------------------------------------------------------------------------
# Cards
# --------------------------------------------------------------------------
def hero():
    H = 280
    c = Card("hero", W, H, T_HERO, f"{PROFILE['name']}: {PROFILE['about']}")
    acc = THEME["accent"]
    c.defs.append(
        f'<pattern id="dots" width="22" height="22" patternUnits="userSpaceOnUse">'
        f'<circle cx="1.5" cy="1.5" r="1.2" fill="{THEME["line"]}"/></pattern>'
        f'<radialGradient id="glow" cx="78%" cy="45%" r="55%">'
        f'<stop offset="0" stop-color="{acc}" stop-opacity=".16"/>'
        f'<stop offset="1" stop-color="{acc}" stop-opacity="0"/></radialGradient>'
        f'<linearGradient id="fadeR" x1="0" x2="1"><stop offset="0" stop-color="#fff" stop-opacity="0"/>'
        f'<stop offset=".55" stop-color="#fff" stop-opacity=".9"/></linearGradient>'
        f'<mask id="dotmask"><rect width="{W}" height="{H}" fill="url(#fadeR)"/></mask>'
        f'<clipPath id="card"><rect width="{W}" height="{H}" rx="16"/></clipPath>'
    )
    c.frame()
    c.add(f'<g clip-path="url(#card)"><rect width="{W}" height="{H}" fill="url(#dots)" mask="url(#dotmask)"/>'
          f'<rect width="{W}" height="{H}" fill="url(#glow)"/></g>')

    # Terminal prompt, typed out character by character
    x, y = 44, 64
    c.add(f'<svg x="{x}" y="{y - 15}" width="27" height="18" viewBox="290 110 750 495"{c.a("pop", 0.0)}>'
          + "".join(f'<polygon points="{pt}" fill="{THEME["text"]}"/>' for pt in LOGO) + "</svg>")
    x += 38
    c.add(c.text(x, y, PROFILE["prompt"], "mono", 15, acc, anim=c.a("fade", 0.0)))
    x += text_width(PROFILE["prompt"], "mono", 15)
    for i, ch in enumerate(PROFILE["command"]):
        c.add(c.text(x, y, ch, "mono", 15, THEME["text"], anim=c.a("type", 0.35 + i * 0.07)))
        x += text_width(ch, "mono", 15)
    c.add(f'<rect x="{x + 2:.1f}" y="{y - 13}" width="8.5" height="16" fill="{acc}" class="s-show">'
          f'{c.blink(0.35 + len(PROFILE["command"]) * 0.07)}</rect>')

    # Name, handle and accent underline
    c.add(c.text(42, 150, PROFILE["name"], "display", 76, anim=c.a("rise", 0.75), ls=-1.5))
    nx = 42 + text_width(PROFILE["name"], "display", 76) - 1.5 * len(PROFILE["name"]) + 18
    c.add(c.text(nx, 150, f"aka {PROFILE['login']}", "mono", 15, THEME["muted"], anim=c.a("fade", 1.05)))
    c.add(f'<rect x="44" y="168" width="64" height="4" rx="2" fill="{acc}"{c.a("wipe", 0.95)}/>')

    # One line about me
    about_size = 19.0
    while about_size > 15 and text_width(PROFILE["about"], "sans", about_size) > 570:
        about_size -= 0.5
    c.add(c.text(44, 210, PROFILE["about"], "sans", about_size, THEME["text"], anim=c.a("rise", 1.0)))
    meta = "   ·   ".join(PROFILE["meta"])
    c.add(c.text(44, 242, meta, "mono", 13, THEME["muted"], anim=c.a("rise", 1.15)))

    portrait = next((ROOT / p for p in PORTRAIT_CANDIDATES if (ROOT / p).exists()), None)
    if portrait:
        cx, cy, r = 720, 140, 84
        mime = "image/png" if portrait.suffix == ".png" else "image/jpeg"
        b64 = base64.b64encode(portrait.read_bytes()).decode()
        c.defs.append(f'<clipPath id="pc"><circle cx="{cx}" cy="{cy}" r="{r}"/></clipPath>')
        c.add(f'<g{c.a("pop", 0.6)}><image href="data:{mime};base64,{b64}" x="{cx - r}" y="{cy - r}" '
              f'width="{2 * r}" height="{2 * r}" preserveAspectRatio="xMidYMid slice" clip-path="url(#pc)"/></g>')
        attrs, anim = c.draw(2 * math.pi * (r + 8), 0.8, 1.2)
        c.add(f'<circle cx="{cx}" cy="{cy}" r="{r + 8}" fill="none" stroke="{acc}" stroke-width="2" '
              f'transform="rotate(-90 {cx} {cy})"{attrs}>{anim}</circle>')
    else:
        handshake(c, acc)
    return c


def handshake(c: Card, acc: str):
    """A small TCP handshake: a nod to SYNACK."""
    lx, rx, top = 656, 796, 62
    for x, label in ((lx, "you"), (rx, "ivan")):
        c.add(c.text(x, top, label, "mono", 12, THEME["muted"], "middle", anim=c.a("fade", 0.3)))
        attrs, anim = c.draw(170, 0.35, 0.7)
        c.add(f'<line x1="{x}" y1="{top + 12}" x2="{x}" y2="{top + 182}" stroke="{THEME["line"]}" '
              f'stroke-width="2"{attrs}>{anim}</line>')
    steps = [("SYN", lx, rx, 104), ("SYN-ACK", rx, lx, 148), ("ACK", lx, rx, 192)]
    for i, (label, x1, x2, y) in enumerate(steps):
        at = 1.0 + i * 0.45
        y2 = y + 14
        length = math.hypot(x2 - x1, y2 - y)
        attrs, anim = c.draw(length, at, 0.45)
        col = acc if label != "SYN-ACK" else THEME["text"]
        c.add(f'<line x1="{x1}" y1="{y}" x2="{x2}" y2="{y2}" stroke="{col}" stroke-width="2"{attrs}>{anim}</line>')
        ang = math.atan2(y2 - y, x2 - x1)
        hx, hy = x2, y2
        p1 = (hx - 10 * math.cos(ang - .45), hy - 10 * math.sin(ang - .45))
        p2 = (hx - 10 * math.cos(ang + .45), hy - 10 * math.sin(ang + .45))
        sattrs, sanim = c.appear(at + 0.4, 0.15)
        c.add(f'<path d="M{p1[0]:.1f} {p1[1]:.1f} L{hx} {hy} L{p2[0]:.1f} {p2[1]:.1f}" fill="none" '
              f'stroke="{col}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"{sattrs}>{sanim}</path>')
        c.add(c.text((x1 + x2) / 2, (y + y2) / 2 - 11, label, "monob", 11, col, "middle",
                     anim=c.a("fade", at + 0.15)))
    c.add(c.text((lx + rx) / 2, 258, "connection established", "mono", 11.5, THEME["faint"],
                 "middle", anim=c.a("fade", 2.45)))


def tile(i: int, t: dict):
    w, h = 400, 228
    c = Card(t["file"], w, h, T_TILES[i], f"{t['label']}: {t['title']}. {t['sub']}")
    acc = THEME["accent"]
    c.frame(18)
    c.add(c.text(30, 50, f"0{i + 1}", "monob", 17, acc, anim=c.a("fade", 0.0)))
    c.add(c.text(64, 50, t["label"].upper(), "mono", 17, THEME["muted"], anim=c.a("fade", 0.08), ls=1.5))
    arrow_icon(c, w - 52, 34, 20, 0.25, acc)
    title_size = 40
    while text_width(t["title"], "display", title_size) > w - 60:
        title_size -= 2
    c.add(c.text(30, 132, t["title"], "display", title_size, anim=c.a("rise", 0.15), ls=-0.5))
    c.add(c.text(30, 170, t["sub"], "sans", 19, THEME["muted"], anim=c.a("rise", 0.25)))
    c.add(f'<rect x="30" y="{h - 26}" width="{w - 60}" height="3" rx="1.5" fill="{THEME["line"]}"/>')
    c.add(f'<rect x="30" y="{h - 26}" width="{(w - 60) * 0.32:.0f}" height="3" rx="1.5" fill="{acc}"{c.a("wipe", 0.3)}/>')
    return c


def role():
    r = ROLE
    H = 330
    c = Card("bcucompsoc", W, H, T_ROLE,
             f"{r['title']}, {r['org']}, {r['dates']}. {r['summary']} "
             + "; ".join(f"{v} {l}" for v, l in r["stats"]) + f". Workshop library: {r['link']}")
    acc = THEME["accent"]
    c.frame()
    c.add(f'<rect x="0" y="24" width="4" height="{H - 48}" rx="2" fill="{acc}"{c.a("grow", 0.05)}/>')
    c.add(c.text(40, 48, "// ROLE", "mono", 12.5, acc, anim=c.a("fade", 0.0), ls=1))
    c.add(c.text(W - 40, 48, r["dates"].upper(), "mono", 12.5, THEME["muted"], "end",
                 anim=c.a("fade", 0.05), ls=1))
    c.add(c.text(40, 90, r["title"], "display", 32, anim=c.a("slide", 0.1), ls=-0.4))
    c.add(c.text(40, 117, r["org"], "sansb", 15, THEME["muted"], anim=c.a("fade", 0.2)))
    c.add(c.text(40, 145, r["summary"], "sans", 15, "#c4ccd4", anim=c.a("rise", 0.25)))

    pw, gap, py = 245, 12, 164
    for i, (value, label) in enumerate(r["stats"]):
        x = 40 + i * (pw + gap)
        c.add(f'<g{c.a("rise", 0.35 + i * 0.1)}>'
              f'<rect x="{x}" y="{py}" width="{pw}" height="80" rx="12" fill="{THEME["panel"]}" stroke="{THEME["line"]}"/>'
              + c.text(x + 18, py + 38, value, "display", 27, acc)
              + c.text(x + 18, py + 62, label, "sans", 13.5, THEME["muted"])
              + "</g>")

    # Workshop pipeline: build -> push -> portfolio
    y, x = 288, 40
    for i, step in enumerate(r["steps"]):
        at = 0.75 + i * 0.3
        tw = text_width(step, "mono", 12) + 26
        c.add(f'<g{c.a("pop", at)}><rect x="{x:.1f}" y="{y - 17}" width="{tw:.1f}" height="26" rx="13" '
              f'fill="{THEME["panel"]}" stroke="{acc if i == len(r["steps"]) - 1 else THEME["line"]}"/>'
              + c.text(x + 13, y, step, "mono", 12, THEME["text"]) + "</g>")
        x += tw
        if i < len(r["steps"]) - 1:
            attrs, anim = c.draw(30, at + 0.15, 0.25)
            c.add(f'<path d="M{x + 6:.1f} {y - 4} H{x + 36:.1f} M{x + 30:.1f} {y - 9} L{x + 36:.1f} {y - 4} '
                  f'L{x + 30:.1f} {y + 1}" fill="none" stroke="{THEME["faint"]}" stroke-width="1.8" '
                  f'stroke-linecap="round" stroke-linejoin="round"{attrs}>{anim}</path>')
            x += 42
    c.add(c.text(W - 64, y, r["link"], "monob", 12.5, acc, "end", anim=c.a("fade", 1.7)))
    arrow_icon(c, W - 56, y - 13, 14, 1.75, acc)
    return c


def chart(s: dict):
    H = 380
    c = Card("activity", W, H, T_CHART,
             f"{s['total']} contributions in the last year. Current streak {s['current']} days, "
             f"{s['active']} active days, busiest day "
             + (f"{fmt_date(s['busiest']['date'])} with {s['busiest']['contributionCount']}" if s["busiest"] else "none"))
    acc = THEME["accent"]
    c.frame()
    c.add(c.text(40, 48, "// ACTIVITY", "mono", 12.5, acc, anim=c.a("fade", 0.0), ls=1))
    c.add(c.text(40, 80, f"{s['total']:,} contributions in the last year", "sansb", 22, anim=c.a("rise", 0.05)))

    # Stat panels
    b = s["busiest"]
    panels = [
        ("CURRENT STREAK", f"{s['current']}", "days" if s["current"] != 1 else "day",
         f"longest {s['longest']} days"),
        ("ACTIVE DAYS", f"{s['active']}", f"/ {s['span']}",
         f"{(100 * s['active'] / s['span']) if s['span'] else 0:.0f}% of the year"),
        ("BUSIEST DAY", f"{b['contributionCount'] if b else 0}", "contributions",
         fmt_date(b["date"]) if b else "-"),
    ]
    pw, gap, px0, py = 245, 12, 40, 104
    for i, (label, value, unit, sub) in enumerate(panels):
        x = px0 + i * (pw + gap)
        at = 0.15 + i * 0.12
        c.add(f'<g{c.a("rise", at)}>'
              f'<rect x="{x}" y="{py}" width="{pw}" height="92" rx="12" fill="{THEME["panel"]}" stroke="{THEME["line"]}"/>'
              + c.text(x + 18, py + 26, label, "mono", 11, THEME["muted"], ls=1)
              + c.text(x + 18, py + 62, value, "display", 32)
              + c.text(x + 24 + text_width(value, "display", 32), py + 62, unit, "sans", 14, THEME["muted"])
              + c.text(x + 18, py + 80, sub, "mono", 11, THEME["faint"])
              + "</g>")

    # Monthly bars
    months = s["months"]
    vals = list(months.values()) or [0]
    peak = max(vals) or 1
    top, base = 232, 330
    n = len(vals)
    slot = (W - 80) / max(n, 1)
    bw = slot * 0.56
    attrs, anim = c.draw(W - 80, 0.5, 0.9)
    c.add(f'<line x1="40" y1="{base + .5}" x2="{W - 40}" y2="{base + .5}" stroke="{THEME["line"]}" '
          f'stroke-width="1.5"{attrs}>{anim}</line>')
    for i, (key, v) in enumerate(months.items()):
        x = 40 + i * slot + (slot - bw) / 2
        bh = max(2.0, (base - top) * v / peak) if v else 2.0
        at = 0.6 + i * 0.06
        is_peak = v == peak and v > 0
        fill = acc if is_peak else mix(THEME["bg"], acc, 0.55)
        c.add(f'<rect x="{x:.1f}" y="{base - bh:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="4" fill="{fill}"{c.a("grow", at)}/>')
        c.add(c.text(x + bw / 2, base - bh - 8, str(v), "monob" if is_peak else "mono", 11,
                     acc if is_peak else THEME["muted"], "middle", anim=c.a("fade", at + 0.5)))
        label = dt.date.fromisoformat(key + "-01").strftime("%b")
        c.add(c.text(x + bw / 2, base + 22, label, "mono", 11, THEME["faint"], "middle",
                     anim=c.a("fade", 0.5 + i * 0.03)))
    return c


def repo_card(i: int, f: dict, repos: dict):
    lines = wrap(f["blurb"], "sans", 14.5, 580)[:3]
    H = 114 + len(lines) * 21 + 48
    r = repos.get(f["repo"].lower(), {})
    c = Card(f"repo-{i + 1}", W, H, T_REPOS[i], f"{f['title']}: {f['blurb']}")
    acc = THEME["accent"]
    c.frame()
    c.add(f'<rect x="0" y="24" width="4" height="{H - 48}" rx="2" fill="{acc}"{c.a("grow", 0.05)}/>')
    c.add(c.text(40, 46, f"0{i + 1}", "monob", 12.5, acc, anim=c.a("fade", 0.0)))
    c.add(c.text(68, 46, f"{PROFILE['login']}/{f['repo']}", "mono", 12.5, THEME["muted"],
                 anim=c.a("fade", 0.05)))
    c.add(c.text(40, 84, f["title"], "display", 28, anim=c.a("slide", 0.1), ls=-0.3))
    tx = 40 + text_width(f["title"], "display", 28) - 0.3 * len(f["title"]) + 14
    c.add(c.text(tx, 84, f["subtitle"], "sans", 15, THEME["muted"], anim=c.a("fade", 0.25)))

    for j, line in enumerate(lines):
        c.add(c.text(40, 114 + j * 21, line, "sans", 14.5, "#c4ccd4", anim=c.a("rise", 0.2 + j * 0.05)))

    # Tag pills
    x, y = 40, H - 30
    for j, tag in enumerate(f["tags"]):
        tw = text_width(tag, "mono", 11) + 20
        c.add(f'<g{c.a("pop", 0.4 + j * 0.06)}><rect x="{x:.1f}" y="{y - 15}" width="{tw:.1f}" height="22" '
              f'rx="11" fill="{THEME["panel"]}" stroke="{THEME["line"]}"/>'
              + c.text(x + 10, y, tag, "mono", 11, THEME["muted"]) + "</g>")
        x += tw + 8

    # Live repo stats (right column)
    sx = W - 40
    arrow_icon(c, sx - 18, 32, 16, 0.3, acc)
    rows = []
    if r:
        rows.append(("star", f"{r.get('stargazerCount', 0)} star{'s' if r.get('stargazerCount', 0) != 1 else ''}"))
        lang = r.get("primaryLanguage") or {}
        if lang.get("name"):
            rows.append(("lang", lang["name"], lang.get("color") or THEME["muted"]))
        if r.get("pushedAt"):
            rows.append(("time", ago(r["pushedAt"])))
    for j, row in enumerate(rows):
        y = 112 + j * 26
        at = 0.35 + j * 0.08
        label = row[1]
        lw = text_width(label, "mono", 12)
        g = [c.text(sx, y, label, "mono", 12, THEME["muted"], "end")]
        ix = sx - lw - 12
        if row[0] == "star":
            g.append(f'<path d="{star_path(ix, y - 4, 6.5)}" fill="{acc}"/>')
        elif row[0] == "lang":
            g.append(f'<circle cx="{ix}" cy="{y - 4}" r="5" fill="{row[2]}"/>')
        else:
            g.append(f'<circle cx="{ix}" cy="{y - 4}" r="5" fill="none" stroke="{THEME["faint"]}" stroke-width="1.5"/>'
                     f'<path d="M{ix} {y - 7} V{y - 4} H{ix + 2.5}" fill="none" stroke="{THEME["faint"]}" stroke-width="1.3"/>')
        c.add(f'<g{c.a("fade", at)}>' + "".join(g) + "</g>")
    return c


LEVELS = {"NONE": 0, "FIRST_QUARTILE": 1, "SECOND_QUARTILE": 2, "THIRD_QUARTILE": 3, "FOURTH_QUARTILE": 4}


def graph(days: list, s: dict):
    cell, gap = 11.5, 3
    step = cell + gap
    left, top = 64, 96
    weeks: list[list[dict]] = []
    for d in days:
        wd = (dt.date.fromisoformat(d["date"]).weekday() + 1) % 7  # Sunday = 0
        if not weeks or wd == 0:
            weeks.append([None] * 7)
        weeks[-1][wd] = d
    H = top + 7 * step + 52
    c = Card("contributions", W, H, T_GRAPH, f"Contribution graph: {s['total']} contributions in the last year")
    acc = THEME["accent"]
    ramp = [THEME["empty"]] + [mix(THEME["bg"], acc, t) for t in (0.3, 0.52, 0.76, 1.0)]
    c.frame()
    c.add(c.text(40, 48, "// CONTRIBUTION GRAPH", "mono", 12.5, acc, anim=c.a("fade", 0.0), ls=1))
    c.add(c.text(W - 40, 48, f"{s['total']:,} in the last year", "mono", 12.5, THEME["muted"], "end",
                 anim=c.a("fade", 0.05)))

    for label, row in (("Mon", 1), ("Wed", 3), ("Fri", 5)):
        c.add(c.text(left - 10, top + row * step + cell - 2, label, "mono", 10, THEME["faint"], "end",
                     anim=c.a("fade", 0.1)))
    last_month = None
    for wi, week in enumerate(weeks):
        first = next((d for d in week if d), None)
        if first:
            m = first["date"][:7]
            if m != last_month and wi < len(weeks) - 1:
                if last_month is not None or week[0] is not None:
                    c.add(c.text(left + wi * step, top - 12, dt.date.fromisoformat(first["date"]).strftime("%b"),
                                 "mono", 10, THEME["faint"], anim=c.a("fade", 0.1 + wi * 0.012)))
                last_month = m
        for wd, d in enumerate(week):
            if not d:
                continue
            lvl = LEVELS.get(d.get("contributionLevel"), None)
            if lvl is None:
                lvl = 0 if not d["contributionCount"] else min(4, 1 + d["contributionCount"] // 3)
            x, y = left + wi * step, top + wd * step
            c.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{cell}" height="{cell}" rx="2.5" fill="{ramp[lvl]}"'
                  f'{c.a("pop", 0.15 + wi * 0.018 + wd * 0.01)}/>')

    ly = top + 7 * step + 26
    c.add(c.text(left, ly + 9, "Less", "mono", 10, THEME["faint"], anim=c.a("fade", 1.1)))
    lx = left + 36
    for k, col in enumerate(ramp):
        c.add(f'<rect x="{lx + k * step:.1f}" y="{ly}" width="{cell}" height="{cell}" rx="2.5" fill="{col}"{c.a("fade", 1.1)}/>')
    c.add(c.text(lx + 5 * step + 6, ly + 9, "More", "mono", 10, THEME["faint"], anim=c.a("fade", 1.1)))
    return c


def main():
    user, days, repos = load_data()
    s = stats(days)
    print(f"Rendering {'STATIC' if STATIC else 'animated'} cards from {DATA}")
    cards = [hero()] + [tile(i, t) for i, t in enumerate(TILES)] + [role(), chart(s)]
    cards += [repo_card(i, f, repos) for i, f in enumerate(FEATURED)] + [graph(days, s)]
    for c in cards:
        c.write()


if __name__ == "__main__":
    main()
