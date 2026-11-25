#!/usr/bin/env python3
"""Render the desk + pi-fleet setup diagram, r/homelab style.

Usage: uv run diagram.py              -> docs/diagram.svg + docs/diagram.png

The desk hardware (router, monitor, machines, KVM, PoE switch, pi stack) comes
from inventory.yaml, hand-placed in this file for one specific desk, so moving
anything means moving coordinates around here too. The pi fleet itself (the
three raspberry pis' names, colors, blurbs, services and apps) is NOT in
inventory.yaml: it's read live from apps.toml / hosts/*/host.toml / dns.toml
via cli.manifest, so this diagram never drifts from the actual fleet config.
Line width encodes link speed; cable style encodes what kind of link it is
(see LEGEND below).
"""

import pathlib
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from cli import manifest as fleet  # noqa: E402

INV = yaml.safe_load((ROOT / "inventory.yaml").read_text())
ICONS = ROOT / "icons"
OUT = ROOT / "docs"

W = 3150  # H is set at the end from the content (see "canvas height")

# ---------------------------------------------------------------- palette
BG = "#0b0f15"
GRID = "#151c26"
PANEL = "#111821"
PANEL2 = "#161f2b"
CHIP = "#19222e"
BORDER = "#253042"
TEXT = "#e6edf3"
MUTED = "#8d9aab"
FAINT = "#5d6a7c"
LINK = "#79c0ff"
HWC = "#c08a5c"  # "physical hardware" accent
DESK = "#58a6ff"  # desk gear (router, monitor, machines, KVM, switch)
VIDEO = "#bc8cff"  # HDMI / DP / USB-C
WIRE = "#8d9aab"  # ethernet
SPEED_W = {"5G": 5.2, "2.5G": 3.2}
SANS = "Fira Sans"
MONO = "Fira Code"


def esc(s):
    import html

    return html.escape(str(s), quote=True)


def tw(s, size, mono=False):
    """Rough text width for layout decisions."""
    return len(str(s)) * size * (0.6 if mono else 0.53)


def _font_file(pattern):
    """Path of an installed font via fontconfig (fc-match), or None."""
    try:
        out = subprocess.run(
            ["fc-match", "-f", "%{file}", pattern], capture_output=True, text=True, timeout=5
        )
        return out.stdout.strip() or None
    except Exception:
        return None


try:
    from PIL import ImageFont

    _FONTS = {
        700: _font_file("Fira Sans:bold"),
        600: _font_file("Fira Sans:semibold"),
        400: _font_file("Fira Sans:regular"),
    }
    if not all(_FONTS.values()):
        raise RuntimeError("Fira Sans not found")
    _FCACHE = {}

    def measure(s, size, weight=700):
        key = (weight, size)
        if key not in _FCACHE:
            _FCACHE[key] = ImageFont.truetype(_FONTS[weight], size=size * 8)
        return _FCACHE[key].getlength(str(s)) / 8
except Exception:  # no Pillow / font file: rough estimate

    def measure(s, size, weight=700):
        return tw_title(s, size)


def tw_title(s, size):
    """Width estimate for bold title text (capitals are wider than lowercase)."""
    return sum(0.68 if c.isupper() else 0.58 if c.isdigit() else 0.5 for c in str(s)) * size


class SVG:
    def __init__(self):
        self.p = []
        self._icon_cache = {}

    def add(self, s):
        self.p.append(s)

    def rect(self, x, y, w, h, rx=10, fill=PANEL, stroke=BORDER, sw=1.2, op=1, dash=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}" opacity="{op}"{d}/>'
        )

    def text(self, x, y, s, size=14, fill=TEXT, weight=400, anchor="start", mono=False, ls=0, op=1):
        fam = MONO if mono else SANS
        lsa = f' letter-spacing="{ls}"' if ls else ""
        self.add(
            f'<text x="{x:.1f}" y="{y:.1f}" font-family="{fam}" font-size="{size}" fill="{fill}" '
            f'font-weight="{weight}" text-anchor="{anchor}" opacity="{op}"{lsa}>{esc(s)}</text>'
        )

    def icon(self, name, x, y, size):
        if name not in self._icon_cache:
            f = ICONS / f"{name}.svg"
            if not f.exists():
                raise SystemExit(f"missing icon '{name}' - run: uv run fetch_icons.py")
            import base64

            self._icon_cache[name] = base64.b64encode(f.read_bytes()).decode()
        self.add(
            f'<image x="{x:.1f}" y="{y:.1f}" width="{size}" height="{size}" '
            f'href="data:image/svg+xml;base64,{self._icon_cache[name]}"/>'
        )

    def line(self, pts, color, sw=3, dash=None, r=10, op=1, cap="round"):
        self.add(
            f'<path d="{rounded(pts, r)}" fill="none" stroke="{color}" stroke-width="{sw}" '
            f'stroke-linecap="{cap}" stroke-linejoin="round" opacity="{op}"'
            + (f' stroke-dasharray="{dash}"' if dash else "")
            + "/>"
        )

    def pill(
        self,
        x,
        y,
        s,
        color,
        size=12,
        fill=None,
        mono=False,
        anchor="start",
        h=None,
        pad=8,
        tcolor=None,
        weight=600,
    ):
        w = tw(s, size, mono) + pad * 2
        h = h or size + 10
        if anchor == "middle":
            x -= w / 2
        elif anchor == "end":
            x -= w
        self.rect(x, y, w, h, rx=h / 2, fill=fill or color + "22", stroke=color + "88", sw=1)
        self.text(
            x + w / 2, y + h / 2 + size * 0.36, s, size, tcolor or color, weight, "middle", mono
        )
        return w

    def label(self, x, y, s, color=MUTED, size=12, anchor="middle", mono=False, bg=BG):
        """Text on a line, with a background knock-out."""
        w = tw(s, size, mono) + 12
        bx = x - w / 2 if anchor == "middle" else (x if anchor == "start" else x - w)
        self.rect(bx, y - size - 2, w, size + 9, rx=5, fill=bg, stroke=bg, sw=0)
        self.text(bx + w / 2, y + 1, s, size, color, 500, "middle", mono)

    def render(self):
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
            + "".join(self.p)
            + "</svg>"
        )


def rounded(pts, r):
    """Orthogonal polyline with rounded corners."""
    if len(pts) == 2:
        return f"M{pts[0][0]},{pts[0][1]} L{pts[1][0]},{pts[1][1]}"
    d = f"M{pts[0][0]},{pts[0][1]}"
    for i in range(1, len(pts) - 1):
        (x0, y0), (x1, y1), (x2, y2) = pts[i - 1], pts[i], pts[i + 1]
        l1 = max(abs(x1 - x0), abs(y1 - y0))
        l2 = max(abs(x2 - x1), abs(y2 - y1))
        rr = min(r, l1 / 2, l2 / 2)
        ax = x1 - (x1 - x0) / l1 * rr if l1 else x1
        ay = y1 - (y1 - y0) / l1 * rr if l1 else y1
        bx = x1 + (x2 - x1) / l2 * rr if l2 else x1
        by = y1 + (y2 - y1) / l2 * rr if l2 else y1
        d += f" L{ax:.1f},{ay:.1f} Q{x1},{y1} {bx:.1f},{by:.1f}"
    d += f" L{pts[-1][0]},{pts[-1][1]}"
    return d


S = SVG()


# ---------------------------------------------------------------- shared pieces
def hw_bar(x, y, h):
    """Left accent bar = this card is a physical box / hardware part."""
    S.add(f'<rect x="{x + 1}" y="{y + 12}" width="4" height="{h - 24}" rx="2" fill="{HWC}"/>')


def hw_tiles(hw, x, y, w, th=58, tg=6):
    """A vertical list of hardware tiles (CPU/GPU/SSD/...); returns the bottom y."""
    for i, hwi in enumerate(hw):
        ty = y + i * (th + tg)
        S.rect(x, ty, w, th, rx=11, fill="#151d29", stroke="#2e3a4c", sw=1.2)
        hw_bar(x, ty, th)
        S.icon(hwi["icon"], x + 12, ty + 15, 28)
        S.text(x + 50, ty + 17, hwi["label"], 10, HWC, 700, ls=1.3)
        S.text(x + 50, ty + 35, hwi["value"], 15.5, TEXT, 700)
        if hwi.get("sub"):
            S.text(x + 50, ty + 50, hwi["sub"], 10.5, MUTED, 400)
    return y + len(hw) * (th + tg)


TAB = 34  # height of the folder tab on physical-device cards


def tab_width(name, type_):
    """Tab fits 'name · type' (name 20px bold, type 14px semibold), measured from the font."""
    return (
        16 + measure(name, 20, 700) + 7 + measure("·", 20, 700) + 7 + measure(type_, 14, 600) + 18
    )


def folder(x, y, w, h, tab_w, fill, stroke, sw):
    """Card body at (x, y, w, h) with a folder tab on its top-left edge, tab_w wide and TAB high."""
    t = y - TAB
    r, rt, sl = 12, 9, 16
    d = (
        f"M{x},{y + h - r} L{x},{t + rt} Q{x},{t} {x + rt},{t} L{x + tab_w - sl:.1f},{t} "
        f"C{x + tab_w - sl / 3:.1f},{t} {x + tab_w - sl / 3:.1f},{y} {x + tab_w + sl / 2:.1f},{y} "
        f"L{x + w - r},{y} Q{x + w},{y} {x + w},{y + r} L{x + w},{y + h - r} Q{x + w},{y + h} {x + w - r},{y + h} "
        f"L{x + r},{y + h} Q{x},{y + h} {x},{y + h - r} Z"
    )
    S.add(f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')


def device_card(x, y, w, h, icons, g, color, fill=PANEL, stroke=None, sw=1.6):
    """Physical device: folder tab 'name · type'; body = icon, model/build, [ip,] OS.

    `g` needs name/type and may have model, os, ip, note (a 4th line under OS).
    """
    folder(x, y, w, h, tab_width(g["name"], g["type"]), fill, stroke or color + "aa", sw)
    S.add(
        f'<text x="{x + 16}" y="{y - 10}" font-family="{SANS}" fill="{TEXT}">'
        f'<tspan font-size="20" font-weight="700">{esc(g["name"])}</tspan>'
        f'<tspan dx="7" font-size="20" font-weight="700" fill-opacity="0.6">·</tspan>'
        f'<tspan dx="7" font-size="14" font-weight="600">{esc(g["type"])}</tspan></text>'
    )
    ix = x + 14
    for ic in icons or []:
        S.icon(ic, ix, y + 15, 54)
        ix += 58
    tx_ = ix + 8
    ty_ = y + 28
    if g.get("model"):
        S.text(tx_, ty_, g["model"], 14, TEXT, 500)
        ty_ += 20
    if g.get("ip"):
        S.text(tx_, ty_, g["ip"], 13, color, 500, mono=True)
        ty_ += 18
    if g.get("os"):
        S.text(tx_, ty_, g["os"], 12, MUTED, 400)
        ty_ += 18
    if g.get("note"):
        S.text(tx_, ty_, g["note"], 12, FAINT, 400)


def jack(x, y, w, h, fill, stroke):
    S.rect(x, y, w, h, rx=5, fill=fill, stroke=stroke, sw=1.4)
    S.add(f'<path d="M{x + 10},{y + h - 8} v-14 h6 v-5 h{w - 32} v5 h6 v14 z" fill="#0b0f15"/>')


def speed_tag(x, y, txt, color, anchor="start"):
    S.label(x, y, txt, color, 11, anchor=anchor, mono=True)


# ---------------------------------------------------------------- background + header
S.add(
    '<defs><pattern id="dots" width="24" height="24" patternUnits="userSpaceOnUse">'
    f'<circle cx="1.5" cy="1.5" r="1.3" fill="{GRID}"/></pattern></defs>'
)
BG_IDX = len(S.p)
S.add("")  # background + dot grid: drawn at the end, once the canvas height is known
S.add("")

meta = INV["meta"]
S.text(40, 70, meta["title"], 40, TEXT, 700, ls=-0.5)
S.text(W - 40, 64, meta["date"], 15, MUTED, 400, "end")

# ================================================================ internet + router
rtr = INV["router"]
RX, RY, RW, RH = 700, 230, 440, 210
GROUP_CX = RX + RW / 2  # shared center for router/monitor/desk-row alignment
cloud_x, cloud_y = GROUP_CX, 130
S.add(
    f'<ellipse cx="{cloud_x}" cy="{cloud_y}" rx="150" ry="30" fill="#1a2433" stroke="#3a4a60" stroke-width="1.6"/>'
)
S.text(cloud_x, cloud_y - 3, "Internet", 18, TEXT, 600, "middle")
S.text(cloud_x, cloud_y + 16, INV["internet"]["note"], 11.5, MUTED, 400, "middle")
S.line([(cloud_x, cloud_y + 30), (cloud_x, RY - TAB)], FAINT, 1.8, dash="2 5", r=0)

device_card(RX, RY, RW, RH, rtr.get("icons"), rtr, DESK)
S.text(RX + 16, RY + 64, rtr["lan_note"], 11.5, HWC, 500)

PORT_Y = RY + RH - 50
PORT_X0, PORT_PITCH = RX + 50, 96


def router_port_x(n):
    return PORT_X0 + (n - 1) * PORT_PITCH


for p in rtr["ports"]:
    px = router_port_x(p["n"])
    S.text(px, PORT_Y - 8, f"port {p['n']}", 10, MUTED, 500, "middle", mono=True)
    jack(px - 18, PORT_Y, 36, 30, "#2a3443", DESK)
ROUTER_BOTTOM = RY + RH

# nest mini: wifi only, dotted to the router (nearest wifi source; not stated explicitly)
nm = INV["nest_mini"]
NMX, NMY, NMW, NMH = RX + RW + 180, RY, 300, 100
device_card(NMX, NMY, NMW, NMH, nm.get("icons"), nm, DESK)
S.line(
    [
        (NMX - 4, NMY + NMH / 2),
        (RX + RW + 70, NMY + NMH / 2),
        (RX + RW + 70, RY + 50),
        (RX + RW, RY + 50),
    ],
    FAINT,
    1.8,
    dash="2 5",
)
S.label((RX + RW + NMX) / 2 + 20, NMY + NMH / 2 - 10, "wifi", FAINT, 10.5)

# ================================================================ monitor
mon = INV["monitor"]
MOW, MOH = 480, 150
MOX, MOY = GROUP_CX - MOW / 2, ROUTER_BOTTOM + 150
device_card(MOX, MOY, MOW, MOH, None, mon, DESK)
S.text(MOX + 16, MOY + MOH - 14, " · ".join(mon["ports"]) + " · ethernet", 11.5, MUTED, 400)

# router port 2 -> monitor ethernet (straight down, with a small jog to the port)
rp2x = router_port_x(2)
S.line(
    [(rp2x, PORT_Y + 30), (rp2x, MOY - 60), (GROUP_CX, MOY - 60), (GROUP_CX, MOY - TAB)],
    WIRE,
    SPEED_W["2.5G"],
)
speed_tag(GROUP_CX + 8, MOY - TAB - 10, "2.5G", WIRE)

# ================================================================ desk row: thinkpad / pc / macbook
UNIT = 600  # column pitch: 360 card + 240 gap (hw tiles live in the gap)
DY = MOY + MOH + 190
TPX, PCX, MBX = GROUP_CX - UNIT - 180, GROUP_CX - 180, GROUP_CX + UNIT - 180
TPW = PCW = MBW = 360
TP, PC, MB = INV["thinkpad"], INV["pc"], INV["macbook"]

device_card(TPX, DY, TPW, 150, TP.get("icons"), TP, DESK)
hw_tiles(TP["hw"], TPX + TPW + 40, DY, 170)

device_card(PCX, DY, PCW, 150, PC.get("icons"), PC, DESK)
hw_tiles(PC["hw"], PCX + PCW + 40, DY, 170)

device_card(MBX, DY, MBW, 150, MB.get("icons"), MB, DESK)

TPC, PCC, MBC = TPX + TPW / 2, PCX + PCW / 2, MBX + MBW / 2  # card centers, for cable anchors

# router -> pc (port 1) / thinkpad (port 4). PC sits right under the monitor, so its cable
# detours around the monitor's right edge instead of cutting through the card; thinkpad's
# column is already clear of the monitor, so a jog above it is enough.
rp1x, rp4x = router_port_x(1), router_port_x(4)
DETOUR_X = MOX + MOW + 50
S.line(
    [
        (rp1x, PORT_Y + 30),
        (rp1x, 500),
        (DETOUR_X, 500),
        (DETOUR_X, DY - 110),
        (PCC, DY - 110),
        (PCC, DY - TAB),
    ],
    WIRE,
    SPEED_W["5G"],
)
speed_tag(PCC + 10, DY - 110 - 8, "5G", WIRE)
S.line([(rp4x, PORT_Y + 30), (rp4x, 470), (TPC, 470), (TPC, DY - TAB)], WIRE, SPEED_W["2.5G"])
speed_tag(TPC + 10, DY - TAB - 10, "2.5G", WIRE)

# macbook: no ethernet in the source notes -> wifi, dotted to the router (assumption, same as nest mini)
S.line([(MBC, DY - TAB), (MBC, RY + RH / 2), (RX + RW, RY + RH / 2)], FAINT, 1.6, dash="2 5")
S.label(MBC + 10, RY + RH / 2 - 10, "wifi", FAINT, 10.5, anchor="start")

# video cables: thinkpad -HDMI-> monitor, pc -DP-> monitor, macbook -USB-C-> monitor (bundled: display+power+eth+kvm leg)
S.line(
    [(TPC, DY - TAB), (TPC, MOY + MOH + 70), (MOX + 50, MOY + MOH + 70), (MOX + 50, MOY + MOH)],
    VIDEO,
    3,
)
speed_tag(MOX + 56, MOY + MOH + 64, "HDMI", VIDEO)
S.line(
    [(PCC, DY - TAB), (PCC, MOY + MOH + 42), (GROUP_CX, MOY + MOH + 42), (GROUP_CX, MOY + MOH)],
    VIDEO,
    3,
)
speed_tag(GROUP_CX + 8, MOY + MOH + 36, "DP", VIDEO)
S.line(
    [
        (MBC, DY - TAB),
        (MBC, MOY + MOH + 100),
        (MOX + MOW - 50, MOY + MOH + 100),
        (MOX + MOW - 50, MOY + MOH),
    ],
    VIDEO,
    3,
)
speed_tag(MOX + MOW - 160, MOY + MOH + 94, "USB-C", VIDEO)
S.text(
    MOX + MOW / 2,
    MOY + MOH + 120,
    "USB-C bundles display + power + eth + one KVM leg",
    10.5,
    FAINT,
    400,
    "middle",
)

# ================================================================ KVM switch
kvm = INV["kvm"]
KW = MBX + MBW - TPX
KY = (
    DY + 150 + 260 + 110
)  # below the tallest hw-tile column (pc: 4 tiles) + gap for the dashed cables
KX = TPX
KH = 60 + len(kvm["peripherals"]) * 24
device_card(KX, KY, KW, KH, None, kvm, DESK)
for i, per in enumerate(kvm["peripherals"]):
    S.text(KX + 20, KY + 52 + i * 24, "·", 13, FAINT, 700)
    S.text(KX + 36, KY + 52 + i * 24, per, 13, MUTED, 400)

for lx in (TPC, PCC, MBC):
    S.line([(lx, DY + 150), (lx, KY - TAB)], MUTED, 2, dash="5 5")
S.label(KX + KW / 2, KY - TAB - 10, "KVM console (USB)", MUTED, 11.5)

# ================================================================ PoE switch + pi fleet
poe = INV["poe_switch"]
POX, POY, POW, POH = RX + RW + 700, ROUTER_BOTTOM + 150, 300, 90
device_card(POX, POY, POW, POH, None, poe, DESK)
rp3x = router_port_x(3)
S.line(
    [(rp3x, PORT_Y + 30), (rp3x, POY - 60), (POX + 40, POY - 60), (POX + 40, POY - TAB)],
    WIRE,
    SPEED_W["5G"],
)
speed_tag(POX + 46, POY - TAB - 10, "5G", WIRE)

pf = INV["pi_fleet"]
PFX, PFY, PFW = POX - 40, POY + POH + 110, POW + 460
CASE_PAD = 70
HOST_H = 150
CASE_H = CASE_PAD + 16 + len(fleet.hosts()) * HOST_H + (len(fleet.hosts()) - 1) * 14
S.rect(PFX, PFY, PFW, CASE_H, rx=16, fill="#0f151e", stroke=BORDER, sw=1.6)
S.text(PFX + 20, PFY + 30, "Pi stack", 18, TEXT, 600)
S.text(PFX + 20, PFY + 50, pf["case_note"], 12, MUTED, 400)
S.line([(POX + POW / 2, POY + POH), (POX + POW / 2, PFY + 10)], WIRE, SPEED_W["5G"])


def pi_card(h, x, y, w, hh):
    col = h.colors["accent"]
    S.rect(x, y, w, hh, rx=12, fill=PANEL, stroke=col + "99", sw=1.5)
    S.add(
        f'<circle cx="{x + 30}" cy="{y + 30}" r="18" fill="{col}22" stroke="{col}" stroke-width="1.6"/>'
    )
    S.text(x + 30, y + 37, h.glyph, 18, col, 600, "middle")
    S.text(x + 58, y + 28, h.name, 18, TEXT, 700)
    S.text(x + 58, y + 46, h.blurb, 12, MUTED, 400)
    storage = pf["storage"].get(h.name)
    if storage:
        S.text(x + w - 16, y + 28, storage, 11, HWC, 500, "end")
    sy = y + 64
    S.text(x + 16, sy, " · ".join(s.split("|")[0] for s in h.services[:6]), 11, FAINT, 400)
    apps = fleet.apps_on(h.name)
    ax, ay = x + 16, sy + 22
    for a in apps:
        ax += S.pill(ax, ay, a.domain, LINK, 10.5, mono=True, pad=6, h=20) + 10
    return hh


hy = PFY + CASE_PAD
for h in fleet.hosts():
    pi_card(h, PFX + 20, hy, PFW - 40, HOST_H - 14)
    hy += HOST_H + 14

S.line(
    [(PFX + PFW * 0.2, PFY + CASE_H), (PFX + PFW * 0.2, PFY + CASE_H + 50)], HWC, 2.4, dash="6 4"
)
S.icon("glyph-power", PFX + PFW * 0.2 - 12, PFY + CASE_H + 54, 24)
S.text(PFX + PFW * 0.2 + 18, PFY + CASE_H + 70, pf["power_note"], 11.5, HWC, 500)

# ================================================================ reachable from the internet
recs = [
    r for r in fleet.records() if r.source in ("app", "record") and not r.domain.startswith("dns.")
]
IX, IY, IW = PFX + PFW + 60, PFY, 420
IH = 60 + len(recs) * 32
S.rect(IX, IY, IW, IH, rx=14, fill=PANEL, stroke=BORDER, sw=1.4)
S.text(IX + 18, IY + 30, "reachable from the internet", 16, TEXT, 600)
S.text(IX + 18, IY + 48, "cloudflared tunnel + caddy on thebe/io", 11.5, MUTED, 400)
for i, r in enumerate(recs):
    ry = IY + 66 + i * 32
    S.rect(IX + 14, ry, IW - 28, 26, rx=7, fill=CHIP, stroke=BORDER, sw=1)
    S.text(IX + 24, ry + 18, r.domain, 12, LINK, 600, mono=True)
    S.text(IX + IW - 24, ry + 18, r.target, 11, FAINT, 500, "end", mono=True)

# ---------------------------------------------------------------- canvas height
# the canvas grows with the content: lowest panel + margin
H = int(max(PFY + CASE_H + 100, IY + IH + 40, KY + KH + 40) + 20)
S.rect(0, 0, W, H, rx=0, fill=BG, stroke=BG, sw=0)
S.rect(0, 0, W, H, rx=0, fill="url(#dots)", stroke="none", sw=0)
dots = S.p.pop()
S.p[BG_IDX], S.p[BG_IDX + 1] = S.p.pop(), dots

OUT.mkdir(exist_ok=True)
svg_path = OUT / "diagram.svg"
svg_path.write_text(S.render())
png_path = OUT / "diagram.png"
subprocess.run(["rsvg-convert", "-z", "1.6", "-o", str(png_path), str(svg_path)], check=True)
print(svg_path, png_path)
