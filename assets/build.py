#!/usr/bin/env python3
"""Builds every image on the profile page.

GitHub strips CSS and scripts from READMEs, so anything playful (sticky notes,
tape, stamps, memes) has to live inside SVG images. Each SVG embeds subsets of
its fonts so it renders the same for every visitor.

    python3 -m venv .venv && .venv/bin/pip install fonttools brotli
    .venv/bin/python assets/build.py

Fonts (SIL OFL) and Twemoji art (CC-BY 4.0) are downloaded into assets/.cache/
on first run; skill icons come from skillicons.dev.
"""
import base64
import html
import io
import math
import random
import re
import subprocess
from collections import defaultdict
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

HERE = Path(__file__).parent
CACHE = HERE / ".cache"

# The palette of the Founder 1:1 page: warm paper, near-black ink, brick red.
PAPER, CARD, INK, INK2, MUTED, LINE = "#F7F5F2", "#FFFDF9", "#1C1917", "#47403C", "#78706B", "#E3DED9"
ACCENT, ACCENT_SOFT, GREEN = "#A7342A", "#F6E5E2", "#2F7D4F"
NOTES = {"yellow": "#FFE68C", "pink": "#FFC8D7", "mint": "#C9EFD6",
         "blue": "#CFE3FF", "orange": "#FFD6AE", "lilac": "#E2D6FF"}
TAPES = ["#F5C842", "#F28FAD", "#7FD1A0", "#8DB8F2", "#F5A86B", "#B9A3F2"]

GF = "https://raw.githubusercontent.com/google/fonts/main/ofl/"
FONT_FILES = {"archivo": GF + "archivo/Archivo%5Bwdth,wght%5D.ttf", "kalam": GF + "kalam/Kalam-Regular.ttf",
              "kalam-bold": GF + "kalam/Kalam-Bold.ttf", "anton": GF + "anton/Anton-Regular.ttf"}
FONTS = {  # family name used in the SVG -> (file, variable-axis instance, fallback stack)
    "Disp": ("archivo", {"wdth": 125, "wght": 800}, "'Arial Black', Impact, sans-serif"),
    "Sub": ("archivo", {"wdth": 100, "wght": 600}, "'Helvetica Neue', Arial, sans-serif"),
    "Body": ("archivo", {"wdth": 100, "wght": 400}, "'Helvetica Neue', Arial, sans-serif"),
    "Hand": ("kalam", None, "'Comic Sans MS', 'Segoe Print', cursive"),
    "HandB": ("kalam-bold", None, "'Comic Sans MS', 'Segoe Print', cursive"),
    "Meme": ("anton", None, "Impact, 'Arial Narrow Bold', sans-serif"),
}
TWEMOJI = "https://cdn.jsdelivr.net/gh/jdecked/twemoji@15.1.0/assets/svg/{}.svg"
ICONS = ["go", "ts", "py", "rust", "java", "nextjs", "react", "angular", "flutter", "tauri",
         "nodejs", "fastapi", "spring", "postgres", "redis", "docker", "cloudflare", "aws", "githubactions"]


def fetch(url, dest):
    # curl rather than urllib: python.org builds of Python on macOS ship
    # without CA certificates, so urllib fails TLS verification there.
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["curl", "-sfL", "--max-time", "60", "-o", str(dest), url], check=True)
    return dest


def load_fonts():
    raw = {k: fetch(u, CACHE / "fonts" / f"{k}.ttf").read_bytes() for k, u in FONT_FILES.items()}
    out = {}
    for fam, (key, axes, _) in FONTS.items():
        font = TTFont(io.BytesIO(raw[key]))
        if axes:
            font = instantiateVariableFont(font, axes)
        buf = io.BytesIO()
        font.save(buf)
        out[fam] = (buf.getvalue(), TTFont(io.BytesIO(buf.getvalue())))
    return out


F = load_fonts()


def measure(fam, text, size, ls=0.0):
    font = F[fam][1]
    cmap, hmtx, upem = font.getBestCmap(), font["hmtx"], font["head"].unitsPerEm
    space = hmtx[cmap[32]][0]
    total = sum(hmtx[cmap[ord(c)]][0] if ord(c) in cmap else space for c in text)
    return total * size / upem + ls * max(len(text) - 1, 0)


def wrap(fam, text, size, width):
    lines, cur = [], ""
    for word in text.split(" "):
        trial = f"{cur} {word}".strip()
        if cur and measure(fam, trial, size) > width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + [cur] if cur else lines


def esc(s):
    return html.escape(s, quote=False)


def inner_svg(path):
    """Body of an SVG file, with the outer <svg> tag removed."""
    s = path.read_text()
    return re.sub(r"^.*?<svg[^>]*>|</svg>\s*$", "", s, flags=re.S)


def emoji(code, x, y, size, extra=""):
    body = inner_svg(fetch(TWEMOJI.format(code), CACHE / "emoji" / f"{code}.svg"))
    return f'<svg x="{x:.1f}" y="{y:.1f}" width="{size}" height="{size}" viewBox="0 0 36 36" {extra}>{body}</svg>'


def icon(name, x, y, size):
    s = fetch(f"https://skillicons.dev/icons?i={name}&theme=dark", CACHE / "icons" / f"{name}.svg").read_text()
    # Several icons ship gradients and clip paths with generic ids; prefix them
    # so icons sharing one SVG cannot pick up each other's definitions.
    s = re.sub(r'id="([^"]+)"', rf'id="{name}-\1"', s)
    s = re.sub(r"url\(#([^)]+)\)", rf"url(#{name}-\1)", s)
    s = s.replace('width="48" height="48"', f'x="{x:.1f}" y="{y:.1f}" width="{size}" height="{size}"', 1)
    return s.strip()


class Doc:
    def __init__(self, name, w, h, title, paper=True):
        self.name, self.w, self.h, self.title, self.paper = name, w, h, title, paper
        self.chars, self.parts, self.css = defaultdict(set), [], []

    def add(self, *parts):
        self.parts.extend(parts)

    def t(self, x, y, text, fam="Body", size=20, fill=INK, anchor=None, ls=None, extra=""):
        """A text element. `text` may be a list of (string, colour) runs."""
        runs = [(text, None)] if isinstance(text, str) else text
        body = ""
        for s, colour in runs:
            self.chars[fam].update(s)
            body += f'<tspan fill="{colour}">{esc(s)}</tspan>' if colour else esc(s)
        a = f' text-anchor="{anchor}"' if anchor else ""
        l = f' letter-spacing="{ls}"' if ls else ""
        return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{fam}, {FONTS[fam][2]}" font-size="{size}" '
                f'fill="{fill}"{a}{l} {extra}>{body}</text>')

    def lines(self, x, y, lines, fam, size, lh, fill=INK, anchor=None):
        return "".join(self.t(x, y + i * lh, s, fam, size, fill, anchor) for i, s in enumerate(lines))

    def render(self):
        faces = []
        for fam, chars in sorted(self.chars.items()):
            font = TTFont(io.BytesIO(F[fam][0]))
            missing = {c for c in chars if c.isprintable() and c != " " and ord(c) not in font.getBestCmap()}
            if missing and not all("ऀ" <= c <= "ॿ" for c in missing):
                raise SystemExit(f"{self.name}: {fam} has no glyph for {''.join(sorted(missing))!r}")
            opts = subset.Options()
            opts.flavor, opts.layout_features, opts.drop_tables = "woff2", ["*"], ["DSIG"]
            sub = subset.Subsetter(opts)
            sub.populate(text="".join(chars) + " ")
            sub.subset(font)
            buf = io.BytesIO()
            font.flavor = "woff2"
            font.save(buf)
            data = base64.b64encode(buf.getvalue()).decode()
            faces.append(f'@font-face{{font-family:{fam};src:url(data:font/woff2;base64,{data}) format("woff2")}}')
        bg = ""
        if self.paper:
            bg = (f'<rect x="1" y="1" width="{self.w - 2}" height="{self.h - 2}" rx="28" fill="{PAPER}" stroke="{LINE}" stroke-width="2"/>'
                  f'<rect x="1" y="1" width="{self.w - 2}" height="{self.h - 2}" rx="28" fill="url(#dots)"/>')
        return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" viewBox="0 0 {self.w} {self.h}" role="img" aria-labelledby="title">
<title id="title">{esc(self.title)}</title>
<style>{''.join(faces)}
{''.join(self.css)}
@media (prefers-reduced-motion: reduce) {{ * {{ animation: none !important; }} }}
</style>
<defs>
<pattern id="dots" width="26" height="26" patternUnits="userSpaceOnUse"><circle cx="3" cy="3" r="1.2" fill="{INK}" fill-opacity=".09"/></pattern>
<filter id="sh" x="-15%" y="-15%" width="130%" height="140%"><feDropShadow dx="0" dy="10" stdDeviation="9" flood-color="#3C1E14" flood-opacity=".16"/><feDropShadow dx="0" dy="2" stdDeviation="2" flood-color="#3C1E14" flood-opacity=".10"/></filter>
<filter id="shs" x="-20%" y="-20%" width="140%" height="150%"><feDropShadow dx="0" dy="4" stdDeviation="3.5" flood-color="#3C1E14" flood-opacity=".22"/></filter>
<filter id="rough" x="-5%" y="-5%" width="110%" height="110%"><feTurbulence type="fractalNoise" baseFrequency=".85" numOctaves="2" seed="4" result="n"/><feColorMatrix in="n" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 -9 6.2" result="m"/><feComposite in="SourceGraphic" in2="m" operator="in"/></filter>
<linearGradient id="sheen" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".35"/><stop offset=".25" stop-color="#fff" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".04"/></linearGradient>
</defs>
{bg}
{''.join(self.parts)}
</svg>
"""

    def save(self):
        (HERE / f"{self.name}.svg").write_text(self.render(), encoding="utf-8")
        print(f"wrote assets/{self.name}.svg  {(HERE / f'{self.name}.svg').stat().st_size // 1024} KB")


# ---------- shared pieces ----------

def tape(x, y, w=118, h=34, rot=-4, colour="#FFFFFF", opacity=".6"):
    # Torn ends: a small zigzag on both short sides.
    zz = 5
    d = (f"M{x - w/2},{y - h/2} H{x + w/2} l-{zz},{h/4} {zz},{h/4} -{zz},{h/4} {zz},{h/4} "
         f"H{x - w/2} l{zz},-{h/4} -{zz},-{h/4} {zz},-{h/4} -{zz},-{h/4} Z")
    return (f'<path d="{d}" fill="{colour}" fill-opacity="{opacity}" stroke="{INK}" stroke-opacity=".06" '
            f'transform="rotate({rot} {x} {y})"/>')


def pin(x, y, size=42):
    return emoji("1f4cc", x - size / 2, y - size * 0.7, size)


def note(cx, cy, w, h, colour, rot, content, fix="tape"):
    """A sticky note centred on (cx, cy); `content` is drawn with (0,0) at its top-left."""
    x0, y0, c = -w / 2, -h / 2, 16
    body = f"M{x0},{y0} H{x0 + w} V{y0 + h - c} Q{x0 + w - 3},{y0 + h - 3} {x0 + w - c * 2.4},{y0 + h} H{x0} Z"
    fold = (f"M{x0 + w},{y0 + h - c} Q{x0 + w - c * 1.1},{y0 + h - c * 0.9} {x0 + w - c * 2.4},{y0 + h} "
            f"Q{x0 + w - c * 0.6},{y0 + h - c * 0.2} {x0 + w},{y0 + h - c} Z")
    fixing = tape(0, y0 + 2, rot=-5 + rot * 0.3) if fix == "tape" else pin(0, y0 + 20) if fix == "pin" else ""
    return (f'<g transform="translate({cx:.1f} {cy:.1f}) rotate({rot})">'
            f'<path d="{body}" fill="{colour}" filter="url(#sh)"/><path d="{body}" fill="url(#sheen)"/>'
            f'<path d="{fold}" fill="#000" fill-opacity=".07"/>'
            f'<g transform="translate({x0} {y0})">{content}</g>{fixing}</g>')


def sticker(cx, cy, w, h, rot, content):
    """A white meme sticker like on the 1:1 page."""
    return (f'<g transform="translate({cx:.1f} {cy:.1f}) rotate({rot})"><g transform="translate({-w / 2} {-h / 2})">'
            f'<rect width="{w}" height="{h}" rx="4" fill="#FFFFFF" filter="url(#sh)"/>{content}</g></g>')


def header(d, num, title, aside=None, x=64, y=150):
    nw = measure("Disp", num, 118)
    out = d.t(x, y, num, "Disp", 118, "none", extra=f'stroke="{ACCENT}" stroke-width="2.2"')
    size = min(54, (d.w - 64 - (x + nw + 26)) / measure("Disp", title, 1))
    out += d.t(x + nw + 26, y - 30, title, "Disp", size, INK)
    if aside:
        out += d.t(x + nw + 30, y + 8, aside, "Hand", 25, ACCENT)
    return out


def hand_arrow(d, path, head, cls=""):
    c = f' class="{cls}" pathLength="1"' if cls else ""
    return (f'<path d="{path}" fill="none" stroke="{ACCENT}" stroke-width="3" stroke-linecap="round"{c}/>'
            f'<path d="{head}" fill="none" stroke="{ACCENT}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>')


DOODLES = {  # 48x48 ink sketches
    "target": '<circle cx="24" cy="24" r="19"/><circle cx="24" cy="24" r="11"/><circle cx="24" cy="24" r="3.5"/><path d="M24 24 L44 6 M38 5 L44 6 L43 12"/>',
    "laptop": '<rect x="7" y="9" width="34" height="23" rx="3"/><path d="M2 39 H46 L41 32 H7 Z"/><path d="M15 20 l5 4 -5 4 M24 29 h8"/>',
    "shield": '<path d="M24 4 L41 10 V23 C41 34 33 41 24 45 C15 41 7 34 7 23 V10 Z"/><path d="M16 24 l6 6 11 -12"/>',
    "eye": '<path d="M4 22 C12 33 36 33 44 22"/><path d="M10 29 l-4 5 M18 32 l-2 6 M30 32 l2 6 M38 29 l4 5"/>',
    "zzz": '<path d="M4 34 h10 l-10 10 h10 M17 20 h13 l-13 13 h13 M32 4 h14 l-14 14 h14"/>',
    "book": '<path d="M24 10 C18 6 10 6 5 8 V40 C10 38 18 38 24 42 C30 38 38 38 43 40 V8 C38 6 30 6 24 10 Z M24 10 V42"/><path d="M10 18 h9 M10 25 h9 M29 18 l3 3 6 -7"/>',
    "cursor": '<path d="M8 6 V42 M3 6 H13 M3 42 H13"/><path d="M20 16 h22 M20 24 h16 M20 32 h20" stroke-dasharray="4 4"/>',
    "kanban": '<rect x="4" y="6" width="12" height="36" rx="2"/><rect x="18" y="6" width="12" height="24" rx="2"/><rect x="32" y="6" width="12" height="30" rx="2"/><path d="M7 14 h6 M7 20 h6 M21 14 h6 M35 14 h6"/>',
    "branch": '<circle cx="12" cy="8" r="4"/><circle cx="12" cy="40" r="4"/><circle cx="36" cy="16" r="4"/><path d="M12 12 V36 M36 20 C36 30 12 26 12 34"/>',
    "sync": '<path d="M38 18 A15 15 0 0 0 11 14 M10 30 A15 15 0 0 0 37 34"/><path d="M38 9 V18 H29 M10 39 V30 H19"/>',
    "mail": '<rect x="4" y="12" width="28" height="22" rx="2"/><path d="M4 14 L18 25 L32 14"/><path d="M36 18 C42 18 46 22 44 28 C46 34 40 38 36 36"/>',
    "pin": '<path d="M24 44 C14 30 9 23 9 17 A15 15 0 0 1 39 17 C39 23 34 30 24 44 Z"/><circle cx="24" cy="17" r="5"/><path d="M8 44 H40" stroke-dasharray="3 4"/>',
}


def doodle(name, x, y, size=48, colour=INK):
    s = size / 48
    return (f'<g transform="translate({x} {y}) scale({s})" fill="none" stroke="{colour}" stroke-width="2.6" '
            f'stroke-linecap="round" stroke-linejoin="round">{DOODLES[name]}</g>')


# ---------- hero ----------

def hero():
    d = Doc("hero", 1200, 720, "Prathamesh Pawar, software engineer in India. Sticky notes: right now, release owner for a "
            "four-app production suite; superpower, making it run on a fresh laptop with zero API keys. "
            "A rubber stamp reads: certified, works on your machine.")
    d.css.append(".thunk{transform-box:fill-box;transform-origin:center;animation:thunk .45s cubic-bezier(.2,1.6,.4,1) .7s both}"
                 "@keyframes thunk{from{transform:scale(1.7);opacity:0}to{transform:scale(1);opacity:1}}"
                 ".draw{stroke-dasharray:1;animation:draw 1s ease-out 1.2s both}@keyframes draw{from{stroke-dashoffset:1}to{stroke-dashoffset:0}}")
    x = 64
    d.add(d.t(x, 96, "SOFTWARE ENGINEER · INDIA · IST (UTC+5:30)", "Sub", 15, ACCENT, ls=2.4))
    size = min(112, 640 / measure("Disp", "Prathamesh", 1))
    y1 = 96 + size * 1.0
    d.add(d.t(x - 4, y1, "Prathamesh", "Disp", size, INK, ls=-1),
          d.t(x - 4, y1 + size * 0.98, [("Pawar", None), (".", ACCENT)], "Disp", size, INK, ls=-1))
    lede = wrap("Body", "I build software end to end: from the first messy conversation, through the architecture "
                "and the code, to the release. And I like the parts most people skip.", 24, 600)
    ly = y1 + size * 0.98 + 62
    d.add(d.lines(x, ly, lede, "Body", 24, 35, INK2))
    # washi-tape labels for the domains
    tx, ty = x, ly + len(lede) * 35 + 34
    for i, label in enumerate(["fintech", "manufacturing", "public sector", "healthcare", "dev tools", "AI agents"]):
        w = measure("Hand", label, 23) + 40
        if tx + w > x + 620:
            tx, ty = x, ty + 52
        rot = [-3, 2, -1.5, 3, -2, 1.5][i]
        d.add(tape(tx + w / 2, ty, w, 38, rot, TAPES[i], ".55"),
              d.t(tx + w / 2, ty + 8, label, "Hand", 23, INK, "middle", extra=f'transform="rotate({rot} {tx + w / 2} {ty})"'))
        tx += w + 12
    # the "scroll" nudge
    end = x + 4 + measure("Hand", "scroll. there are memes.", 27) + 14
    d.add(d.t(x + 4, 676, "scroll. there are memes.", "Hand", 27, ACCENT, extra='transform="rotate(-2 70 676)"'),
          hand_arrow(d, f"M{end} 662 C {end + 40} 648, {end + 60} 674, {end + 48} 700",
                     f"M{end + 36} 690 L{end + 48} 702 L{end + 58} 688", "draw"))

    yellow = (d.t(28, 52, "right now:", "HandB", 31, INK) +
              d.lines(28, 96, wrap("Hand", "release owner for a 4-app production suite. pre-qa, then qa, then main. "
                                   "no Friday surprises.", 25, 290), "Hand", 25, 34, INK2))
    pink = (d.t(28, 62, "superpower:", "HandB", 31, INK) +
            d.lines(28, 104, wrap("Hand", "making it run on a fresh laptop with zero API keys.", 25, 250), "Hand", 25, 34, INK2))
    d.add(note(958, 200, 344, 250, NOTES["yellow"], -3, yellow),
          note(1012, 452, 302, 216, NOTES["pink"], 4, pink, fix="pin"))

    # rubber stamp
    w, h = 372, 140
    stamp = (f'<rect x="0" y="0" width="{w}" height="{h}" rx="14" fill="none" stroke="{ACCENT}" stroke-width="5"/>'
             f'<rect x="9" y="9" width="{w - 18}" height="{h - 18}" rx="8" fill="none" stroke="{ACCENT}" stroke-width="2"/>'
             + d.t(w / 2, 38, "CERTIFIED", "Sub", 15, ACCENT, "middle", ls=6)
             + d.t(w / 2, 82, "WORKS ON YOUR", "Disp", 28, ACCENT, "middle")
             + d.t(w / 2, 118, "MACHINE", "Disp", 30, ACCENT, "middle"))
    d.add(f'<g transform="translate({724 - w / 2} {624 - h / 2}) rotate(-8 {w / 2} {h / 2})" opacity=".9">'
          f'<g class="thunk"><g filter="url(#rough)">{stamp}</g></g></g>')
    d.add(d.t(752, 712, "(not just mine)", "Hand", 21, MUTED, "middle", extra='transform="rotate(-8 752 712)"'))
    d.save()


# ---------- how I work ----------

def how():
    d = Doc("how-i-work", 1200, 860, "How I work: business problem first; zero API keys to run; AI gets your permissions, "
            "never more; privacy is a design input; boring releases; docs a newcomer can follow.")
    d.add(header(d, "01", "How I work", "six rules, learned the hard way"))
    items = [
        ("target", "Business problem first", "A real-estate site needed SEO, not React. Shipped it in Astro on Cloudflare with almost zero JavaScript."),
        ("laptop", "Zero API keys to run", "Every outside service has a mock. A new teammate, or an agent, runs the whole thing on day one."),
        ("shield", "AI gets your permissions. Never more.", "Agents call the same audited actions as the UI. There is no delete tool. On purpose."),
        ("eye", "Privacy is a design input", "Mote reads only the field you're typing in, skips passwords, and never stores a word."),
        ("zzz", "Boring releases", "pre-qa, then qa, then main. Checklists, UAT, sign-offs. Production should never be a surprise."),
        ("book", "Docs a newcomer can follow", "A check after every step. Humans and Claude Code both make it to the end."),
    ]
    colours = ["yellow", "pink", "mint", "blue", "orange", "lilac"]
    rots = [-3, 2.2, -1.4, 2.6, -2.2, 1.6]
    jitter = [(0, 0), (6, 18), (-8, -4), (10, 8), (-4, 20), (6, -6)]
    w, h = 336, 262
    for i, (dd, title, body) in enumerate(items):
        col, row = i % 3, i // 3
        cx, cy = 218 + col * 382 + jitter[i][0], 372 + row * 300 + jitter[i][1]
        tl = wrap("HandB", title, 29, w - 100)
        content = doodle(dd, w - 70, 22, 46)
        content += d.lines(26, 52, tl, "HandB", 29, 33, INK)
        content += d.lines(26, 52 + len(tl) * 33 + 10, wrap("Hand", body, 22, w - 50), "Hand", 22, 29, INK2)
        d.add(note(cx, cy, w, h, NOTES[colours[i]], rots[i], content, fix="pin" if i % 2 else "tape"))
    d.save()


# ---------- memes ----------

def memes():
    d = Doc("memes", 1200, 930, "Four memes. Expanding brain about AI permissions: give the agent the database password, "
            "write a stricter prompt, route its tool calls through the UI's permission checks, don't build a delete tool. "
            "Onboarding: no to 'send me your .env', yes to everything running on mocks. One does not simply push to main. "
            "Nobody: me at 1 a.m. writing SETUP.md with a check after every step.")
    d.add(header(d, "02", "Memes, engineering edition", "hand-drawn, because I respect copyright too"))

    # expanding brain
    w, rows, rh, left = 392, 4, 118, 232
    texts = ["Give the agent the database password",
             "Write a stricter system prompt",
             "Send its tool calls through the same permission checks as the UI",
             "Don't build a delete tool at all"]
    bgs = ["#3B3B3B", "#3E4AA8", "#6C4DF2", "#FFF4B8"]
    c = ""
    for i in range(rows):
        y = 12 + i * rh
        c += f'<rect x="12" y="{y}" width="{left}" height="{rh}" fill="#FFFFFF" stroke="{INK}" stroke-width="2"/>'
        lines = wrap("Sub", texts[i], 19, left - 28)
        top = y + (rh - len(lines) * 24) / 2 + 18
        c += d.lines(26, top, lines, "Sub", 19, 24, INK)
        bx = 12 + left
        c += f'<rect x="{bx}" y="{y}" width="{w - 24 - left}" height="{rh}" fill="{bgs[i]}" stroke="{INK}" stroke-width="2"/>'
        cx, cy = bx + (w - 24 - left) / 2, y + rh / 2
        if i >= 1:
            glow = ["#8EA2FF", "#C9B8FF", "#FFFFFF"][i - 1]
            c += f'<circle cx="{cx}" cy="{cy}" r="{30 + i * 9}" fill="{glow}" fill-opacity="{.25 + i * .12}"/>'
        if i == 3:
            c += "".join(f'<path d="M{cx} {cy} L{cx + 52 * math.cos(a * math.pi / 4):.1f} {cy + 50 * math.sin(a * math.pi / 4):.1f}" '
                         f'stroke="#FFB800" stroke-width="5" stroke-linecap="round" opacity=".7"/>' for a in range(8))
            c += emoji("2728", cx + 22, cy - 50, 30)
        c += emoji("1f9e0", cx - 30, cy - 30, 60)
    c += d.t(14, 12 + rows * rh + 34, "AI permissions, in four stages of enlightenment", "Body", 17, INK2)
    d.add(sticker(258, 560, w, 12 + rows * rh + 52, -2, c))

    # drake-style yes/no
    w, rh, face = 372, 150, 140
    c = ""
    for i, (code, txt, bg) in enumerate([("1f645", "“Can you send me your .env so I can run it?”", "#FCE3DE"),
                                         ("1f60e", "One command. Every outside service runs on a mock. Zero keys.", "#DFF3E6")]):
        y = 12 + i * rh
        c += f'<rect x="12" y="{y}" width="{face}" height="{rh}" fill="{bg}" stroke="{INK}" stroke-width="2"/>'
        c += emoji(code, 12 + face / 2 - 46, y + rh / 2 - 46, 92)
        c += f'<rect x="{12 + face}" y="{y}" width="{w - 24 - face}" height="{rh}" fill="#FFFFFF" stroke="{INK}" stroke-width="2"/>'
        lines = wrap("Sub", txt, 21, w - 24 - face - 30)
        c += d.lines(12 + face + 16, y + (rh - len(lines) * 27) / 2 + 20, lines, "Sub", 21, 27, INK)
    c += d.t(14, 12 + 2 * rh + 34, "Onboarding, before and after", "Body", 17, INK2)
    d.add(sticker(668, 400, w, 12 + 2 * rh + 52, 1.8, c))

    # one does not simply
    w, h = 300, 300
    c = f'<rect x="12" y="12" width="{w - 24}" height="{h - 66}" fill="#2B2826"/>'
    c += emoji("1f9d9", w / 2 - 50, 82, 100)
    for y, s in ((64, "ONE DOES NOT SIMPLY"), (h - 72, "PUSH TO MAIN")):
        c += d.t(w / 2, y, s, "Meme", 34, "#FFFFFF", "middle", extra=f'stroke="{INK}" stroke-width="5" paint-order="stroke" stroke-linejoin="round"')
    c += d.t(14, h - 22, "pre-qa, then qa, then main. Always.", "Body", 17, INK2)
    d.add(sticker(1010, 380, w, h, -2.4, c))

    # nobody / me
    w, h = 610, 290
    c = (d.t(28, 52, "Nobody:", "Sub", 22, INK) + d.t(28, 86, "Absolutely nobody:", "Sub", 22, INK)
         + d.t(28, 128, "Me at 1 a.m.:", "Disp", 24, INK))
    c += (f'<g transform="translate(292 30) rotate(2)"><rect width="288" height="196" rx="8" fill="#F6F8FA" stroke="{LINE}" stroke-width="2"/>'
          + d.t(18, 34, "SETUP.md", "Sub", 18, INK)
          + "".join(d.t(18, 72 + i * 36, s, "Body", 17, INK2)
                    + f'<path d="M{246} {64 + i * 36} l6 7 12 -14" fill="none" stroke="{GREEN}" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"/>'
                    for i, s in enumerate(["1. Start Postgres", "2. Run make up", "3. Open localhost:3000", "4. Sign in as demo"]))
          + "</g>")
    c += d.lines(28, 186, wrap("Hand", "every step gets a check. every. single. one.", 23, 240), "Hand", 23, 30, ACCENT)
    c += d.t(14, h - 22, "A setup guide nobody asked for, and everybody uses", "Body", 17, INK2)
    d.add(sticker(836, 752, w, h, 1.2, c))
    d.save()


# ---------- projects ----------

def projects_title():
    d = Doc("built", 1200, 200, "Things I've built. Tap a card: they're all real repos.")
    d.add(header(d, "03", "Things I've built", "tap a card. they're all real repos.", y=146))
    d.save()


CARDS = [
    ("mote", "Mote", "yellow", "cursor", "AI that stays in the flow: completions, offline spellcheck and a prompt enhancer, in any app.",
     ["Rust", "Tauri 2", "React", "Groq"], "reads Hinglish & मराठी"),
    ("taskforge", "TaskForge", "pink", "kanban", "AI-first ticketing. The Coder agent opens PRs and fixes them from its own CI logs.",
     ["Next.js 15", "Prisma", "Postgres", "MCP"], "agents can't delete stuff. on purpose."),
    ("ideavault", "IdeaVault", "mint", "branch", "A thinking system: ideas get branches, checkpoints and decisions with reasons, then become specs.",
     ["Go", "pgvector", "Next.js 16", "5 AI providers"], "works offline too"),
    ("context-sync", "Context Sync", "blue", "sync", "VS Code extension: Claude Code's memory and chats, in a git repo you own.",
     ["TypeScript", "VS Code API", "git"], "the owner reviews every change"),
    ("zoho-to-sharepoint", "Zoho → SharePoint", "orange", "mail", "Chrome extension that opens Zoho Mail attachments straight in Office for the web.",
     ["JavaScript", "Chrome MV3", "OneDrive"], "no server. no keys."),
    ("orbits", "Orbits", "lilac", "pin", "Reminders that fire at a place, not a time.",
     ["React 19", "Leaflet", "Vite"], "“buy meds” rings at any pharmacy"),
]


def card(i, slug, name, colour, dd, line, chips, scribble):
    d = Doc(f"card-{slug}", 600, 400, f"{name}: {line} Built with {', '.join(chips)}.", paper=False)
    w, h, band = 520, 330, 100
    rot = [-1.6, 1.2, 1.4, -1.2, -1.4, 1.6][i]
    size = min(42, (w - 130) / measure("Disp", name, 1))
    c = (f'<rect width="{w}" height="{h}" rx="6" fill="{CARD}" filter="url(#sh)"/>'
         f'<path d="M0 6 A6 6 0 0 1 6 0 H{w - 6} A6 6 0 0 1 {w} 6 V{band} H0 Z" fill="{NOTES[colour]}"/>'
         + doodle(dd, w - 82, 24, 52)
         + d.t(30, band / 2 + size * 0.36, name, "Disp", size, INK)
         + d.lines(30, band + 44, wrap("Body", line, 21, w - 60), "Body", 21, 29, INK2))
    cx = 30
    for chip in chips:
        cw = measure("Sub", chip, 15) + 26
        c += (f'<rect x="{cx}" y="{h - 100}" width="{cw}" height="30" rx="15" fill="none" stroke="{INK}" stroke-opacity=".28" stroke-width="1.5"/>'
              + d.t(cx + cw / 2, h - 80, chip, "Sub", 15, INK, "middle"))
        cx += cw + 8
    # The note stays on the card: on GitHub's dark theme, red ink on the
    # transparent area around the card would all but disappear.
    sw = measure("Hand", scribble, 23)
    c += d.t(w - 28 - sw, h - 26, scribble, "Hand", 23, ACCENT, extra=f'transform="rotate(-2 {w - 28 - sw} {h - 26})"')
    c += tape(w / 2, 0, 120, 34, -3 + rot)
    d.add(f'<g transform="translate({300 - w / 2} {48}) rotate({rot} {w / 2} {h / 2})">{c}</g>')
    d.save()


# ---------- work ----------

def work():
    jobs = [
        ("May 2026 – now", "Bigstrum Solutions", "Software Engineer", [
            "Release owner for a four-app production suite used on a factory floor: a Flutter tablet app, an MIS portal, and a public website with its CMS.",
            "Enterprise apps on Angular, Spring Boot and Keycloak. MFA is mandatory and every access decision comes from central policy.",
            "A Go service and a Tauri tray app for shift check-ins, with every integration mocked so it runs locally on day one."]),
        ("Jul 2025 – May 2026", "LetsLance", "Software Development Engineer", [
            "Architected a production fintech backend: UPI, cards, BBPS, virtual accounts, KYC and OTP.",
            "Sole developer of Zonikk, a live industrial and commercial real-estate platform.",
            "Backend for 1,000+ student submissions with role-based access, Razorpay and PDF invoices."]),
        ("Jan – Jun 2025", "Infrawave Solutions", "Full-stack intern", [
            "An event platform (Stripe, wallets, QR entry), a pharma CMS on Next.js and S3, and a marketing platform's UI."]),
        ("2021 – 2025", "IIIT Vadodara", "B.Tech, Computer Science and Engineering", []),
    ]
    blocks, y = [], 268
    d = Doc("work", 1200, 100, "")
    for date, org, role, bullets in jobs:
        lines = [wrap("Body", b, 19, 600) for b in bullets]
        blocks.append((y, date, org, role, lines))
        y += 78 + sum(len(l) * 27 + 8 for l in lines) + 34
    d.h = y + 30
    d.title = ("Where I've worked: Bigstrum Solutions, software engineer, May 2026 to now; LetsLance, software development "
               "engineer, July 2025 to May 2026; Infrawave Solutions, full-stack intern, January to June 2025; "
               "IIIT Vadodara, B.Tech in computer science, 2021 to 2025.")
    d.add(header(d, "04", "Where I've worked", "the serious bit. briefly."))
    top, bottom = 210, d.h - 34
    d.add(f'<rect x="44" y="{top}" width="790" height="{bottom - top}" rx="6" fill="{CARD}" filter="url(#sh)"/>')
    d.add("".join(f'<path d="M44 {yy} H834" stroke="#DCE8F5" stroke-width="1.5"/>' for yy in range(top + 52, bottom - 10, 34)))
    d.add(f'<path d="M126 {top} V{bottom}" stroke="#E9A6A0" stroke-width="2"/>')
    d.add("".join(f'<circle cx="80" cy="{yy}" r="9" fill="{PAPER}" stroke="{LINE}" stroke-width="1.5"/>' for yy in range(top + 70, bottom - 30, 150)))
    first, last = blocks[0][0], blocks[-1][0]
    d.add(f'<path d="M160 {first - 8} C 150 {first + 120}, 172 {(first + last) / 2}, 160 {last - 8}" fill="none" stroke="{INK}" stroke-width="2.5" stroke-dasharray="2 7" stroke-linecap="round"/>')
    for y, date, org, role, lines in blocks:
        d.add(f'<circle cx="160" cy="{y - 10}" r="9" fill="{ACCENT}"/>',
              d.t(186, y, org, "Disp", 30, INK),
              d.t(186 + measure("Disp", org, 30) + 16, y, role, "Sub", 17, MUTED),
              d.t(186, y + 32, date, "Hand", 22, ACCENT))
        yy = y + 70
        for l in lines:
            d.add(f'<path d="M190 {yy - 7} h10" stroke="{ACCENT}" stroke-width="2.4" stroke-linecap="round"/>',
                  d.lines(212, yy, l, "Body", 19, 27, INK2))
            yy += len(l) * 27 + 8
    lang = (d.lines(26, 54, ["languages I've", "shipped in:"], "HandB", 26, 30, INK)
            + d.lines(26, 128, ["Go · TypeScript", "Python · Rust · Java"], "Hand", 25, 34, INK2))
    fact = (d.t(26, 58, "fun fact:", "HandB", 26, INK)
            + d.lines(26, 98, wrap("Hand", "GitHub doesn't allow CSS in a README, so every note on this page is hand-built SVG.", 23, 220), "Hand", 23, 31, INK2))
    flex = (d.t(26, 58, "biggest flex:", "HandB", 26, INK)
            + d.lines(26, 98, wrap("Hand", "production releases so boring nobody remembers them.", 24, 220), "Hand", 24, 32, INK2))
    d.add(note(1010, 340, 290, 230, NOTES["yellow"], 3, lang),
          note(1004, 630, 280, 220, NOTES["mint"], -2.5, flex, fix="pin"),
          note(1012, 925, 290, 250, NOTES["blue"], 2, fact))
    d.save()


# ---------- stack ----------

def stack():
    d = Doc("stack", 1200, 700, "My stack as laptop stickers: Go, TypeScript, Python, Rust, Java, Next.js, React, Angular, "
            "Flutter, Tauri, Node.js, FastAPI, Spring, PostgreSQL, Redis, Docker, Cloudflare, AWS, GitHub Actions, plus "
            "MCP, pgvector, Keycloak, Razorpay and UPI, Playwright, zero API keys, and 'I like boring releases'.")
    d.add(header(d, "05", "Stack", "as seen on my laptop lid"))
    lx, ly, lw, lh = 90, 196, 1020, 470
    d.add(f'<rect x="{lx}" y="{ly}" width="{lw}" height="{lh}" rx="34" fill="#2B2826" filter="url(#sh)"/>'
          f'<rect x="{lx + 10}" y="{ly + 10}" width="{lw - 20}" height="{lh - 20}" rx="26" fill="none" stroke="#3B3633" stroke-width="2"/>'
          f'<circle cx="600" cy="{ly + lh / 2}" r="40" fill="#3B3633"/>'
          + d.t(600, ly + lh / 2 + 15, "P.", "Disp", 40, PAPER, "middle"))
    rnd = random.Random(11)
    size = 74

    def icon_sticker(name, cx, cy):
        r = rnd.uniform(-14, 14)
        pad = 7
        return (f'<g transform="translate({cx:.1f} {cy:.1f}) rotate({r:.1f})">'
                f'<rect x="{-size / 2 - pad}" y="{-size / 2 - pad}" width="{size + pad * 2}" height="{size + pad * 2}" rx="26" fill="#FFFFFF" filter="url(#shs)"/>'
                f'{icon(name, -size / 2, -size / 2, size)}</g>')

    top, bottom = ICONS[:10], ICONS[10:]
    for i, n in enumerate(top):
        d.add(icon_sticker(n, lx + 80 + i * 96 + rnd.uniform(-6, 6), ly + 82 + rnd.uniform(-8, 8)))
    for i, n in enumerate(bottom):
        d.add(icon_sticker(n, lx + 128 + i * 96 + rnd.uniform(-6, 6), ly + lh - 82 + rnd.uniform(-8, 8)))
    texts = [("MCP", ACCENT, PAPER), ("pgvector", NOTES["mint"], INK), ("Keycloak", NOTES["blue"], INK),
             ("pre-qa → qa → main", "#FFFFFF", INK), ("Razorpay · UPI", NOTES["yellow"], INK),
             ("Playwright", NOTES["lilac"], INK), ("zero API keys", NOTES["pink"], INK), ("I like boring releases", INK, NOTES["yellow"])]
    spots = [(250, 338), (430, 322), (220, 410), (420, 400), (790, 330), (980, 338), (800, 410), (980, 402)]
    for (label, bg, fg), (cx, cy) in zip(texts, spots):
        w = measure("Sub", label, 19) + 36
        r = rnd.uniform(-7, 7)
        d.add(f'<g transform="translate({cx} {cy + ly - 196 + 30}) rotate({r:.1f})">'
              f'<rect x="{-w / 2}" y="-22" width="{w}" height="44" rx="22" fill="{bg}" stroke="#FFFFFF" stroke-width="5" filter="url(#shs)"/>'
              + d.t(0, 7, label, "Sub", 19, fg, "middle") + "</g>")
    d.save()


# ---------- contact ----------

def contact():
    d = Doc("contact", 1200, 560, "Let's talk. If you're building something where the software has to be right, money moving, "
            "health records, AI that acts on someone's behalf, write to me at pratham1024@gmail.com. "
            "Meme: me refreshing my inbox after you read this far.")
    d.css.append(".peek{animation:peek 3.2s ease-in-out 1s infinite}@keyframes peek{0%,40%,100%{transform:translateX(0)}50%,90%{transform:translateX(-7px)}}")
    d.add(header(d, "06", "Let's talk", "psst: this whole picture is a link."))
    w, h = 660, 300
    msg = wrap("Hand", "If you're building something where the software has to be right (money moving, health records, "
               "AI that acts on someone's behalf, a system a whole team leans on), I'd like to hear about it.", 26, w - 70)
    c = (f'<rect width="{w}" height="{h}" rx="6" fill="{CARD}" filter="url(#sh)"/>'
         + "".join(f'<path d="M0 {yy} H{w}" stroke="#DCE8F5" stroke-width="1.5"/>' for yy in range(70, h - 20, 36))
         + d.lines(34, 66, msg, "Hand", 26, 36, INK2)
         + d.t(34, h - 66, "pratham1024@gmail.com", "Disp", 30, ACCENT)
         + d.t(34, h - 32, "linkedin.com/in/prathameshppawar", "Sub", 18, MUTED)
         + tape(w / 2, 0, 130, 34, -4))
    d.add(f'<g transform="translate(64 198) rotate(-1.2 {w / 2} {h / 2})">{c}</g>')
    mw, mh = 340, 320
    m = f'<rect x="12" y="12" width="{mw - 24}" height="{mh - 66}" fill="#2B2826"/>'
    m += f'<g class="peek">{emoji("1f440", mw / 2 - 62, 82, 124)}</g>'
    for y, s in ((60, "ME REFRESHING MY INBOX"), (mh - 74, "AFTER YOU SCROLLED THIS FAR")):
        m += d.t(mw / 2, y, s, "Meme", 26, "#FFFFFF", "middle", extra=f'stroke="{INK}" stroke-width="5" paint-order="stroke" stroke-linejoin="round"')
    m += d.t(14, mh - 22, "no pressure. (some pressure.)", "Body", 17, INK2)
    d.add(sticker(946, 340, mw, mh, 2.4, m))
    d.add(d.t(600, d.h - 22, "India · IST (UTC+5:30) · I write English, हिंदी and मराठी, and so does my software.", "Hand", 21, MUTED, "middle"))
    d.save()


if __name__ == "__main__":
    hero()
    how()
    memes()
    projects_title()
    for i, c in enumerate(CARDS):
        card(i, *c)
    work()
    stack()
    contact()
