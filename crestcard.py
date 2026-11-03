"""Crest card on the team's own colours (user, 2026-11-03: never a bare crest on white)."""
import io, re, urllib.request, colorsys, unicodedata
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

W, H = 1200, 675
UA = {"User-Agent": "Mozilla/5.0"}

# official colours (main, second) for the clubs we write about most; others come from the crest itself
CLUB_COLOURS = {
    "panathinaikos": ("#0a7a3b", "#ffffff"), "olympiacos": ("#c8102e", "#ffffff"), "aek": ("#f6c400", "#111111"),
    "paok": ("#111111", "#ffffff"), "aris": ("#f6c400", "#111111"), "ofi": ("#111111", "#d8262e"),
    "asteras": ("#1a3c8c", "#f6c400"), "volos": ("#1d3f8f", "#e30613"), "atromitos": ("#1c3f94", "#ffffff"),
    "levadiakos": ("#0a7a3b", "#ffffff"), "panetolikos": ("#f6c400", "#1c3f94"), "kifisia": ("#ffffff", "#1c3f94"),
    "panserraikos": ("#c8102e", "#ffffff"), "larissa": ("#7a1630", "#ffffff"), "iraklis": ("#1c3f94", "#ffffff"),
    "peristeri": ("#f26522", "#1c2c6b"), "promitheas": ("#f6c400", "#111111"), "kolossos": ("#1c3f94", "#ffffff"),
    "panionios": ("#c8102e", "#1c2c6b"), "lavrio": ("#1c3f94", "#f6c400"), "karditsa": ("#2b5aa8", "#ffffff"),
    "maroussi": ("#5b2c86", "#ffffff"), "real madrid": ("#1c2c6b", "#f2c94c"), "barcelona": ("#a50044", "#004d98"),
    "lakers": ("#552583", "#fdb927"), "celtics": ("#007a33", "#ffffff"), "warriors": ("#1d428a", "#ffc72c"),
    "bucks": ("#00471b", "#eee1c6"), "heat": ("#98002e", "#f9a01b"), "knicks": ("#006bb6", "#f58426"),
    "fenerbahce": ("#002d72", "#f6c400"), "efes": ("#0a2d64", "#e30613"), "partizan": ("#111111", "#ffffff"),
    "zvezda": ("#c8102e", "#ffffff"), "crvena": ("#c8102e", "#ffffff"), "maccabi": ("#f6c400", "#1c3f94"),
    "monaco": ("#c8102e", "#ffffff"), "milano": ("#c8102e", "#111111"), "olimpia": ("#c8102e", "#111111"),
    "baskonia": ("#1c2c6b", "#c8102e"), "valencia": ("#f26522", "#111111"), "bayern": ("#dc052d", "#0066b2"),
    "liverpool": ("#c8102e", "#00b2a9"), "manchester united": ("#da291c", "#111111"), "manchester city": ("#6cabdd", "#1c2c5b"),
    "arsenal": ("#ef0107", "#ffffff"), "chelsea": ("#034694", "#ffffff"), "juventus": ("#111111", "#ffffff"),
    "inter": ("#0068a8", "#111111"), "milan": ("#c8102e", "#111111"), "lazio": ("#87d8f7", "#1c2c5b"),
}


def _hex(h):
    h = h.lstrip("#"); return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _plain(s):
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower()) if not unicodedata.combining(c))


def load_logo(url):
    if url.startswith("file://"):
        return Image.open(url[7:]).convert("RGBA")
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return Image.open(io.BytesIO(r.read())).convert("RGBA")


def crest_colours(logo):
    small = logo.copy(); small.thumbnail((90, 90))
    buckets = {}
    for r, g, b, a in small.getdata():
        if a < 160:
            continue
        h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        if s < 0.3 or l < 0.12 or l > 0.88:
            continue
        c = buckets.setdefault(round(h * 18) % 18, [0, 0, 0, 0]); c[0] += r; c[1] += g; c[2] += b; c[3] += 1
    tops = [c for c in sorted(buckets.values(), key=lambda c: -c[3]) if c[3] >= 6]
    cols = [(c[0] // c[3], c[1] // c[3], c[2] // c[3]) for c in tops[:2]]
    if not cols:
        return (20, 20, 24), (235, 235, 235)
    if len(cols) == 1:
        cols.append((255, 255, 255))
    return cols[0], cols[1]


def lum(c):
    return (0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]) / 255


def crest_card(path, logo_url, team=""):
    logo = load_logo(logo_url)
    key = next((k for k in CLUB_COLOURS if k in _plain(team)), None)
    c1, c2 = (tuple(map(_hex, CLUB_COLOURS[key])) if key else crest_colours(logo))
    # light main colour (yellow / white): dark base in the second colour, stripes in the main one
    if lum(c1) > 0.62:
        base, accent = (c2 if lum(c2) < 0.45 else (18, 18, 22)), c1
    else:
        base, accent = c1, (c2 if lum(c2) > 0.5 else (255, 255, 255))
    base = np.array(base, float); accent = np.array(accent, float)
    yy, xx = np.mgrid[0:H, 0:W]
    # radial: brighter in the middle, deep at the edges
    r = np.sqrt(((xx - W / 2) / (W * 0.62)) ** 2 + ((yy - H / 2) / (H * 0.75)) ** 2)
    shade = np.clip(1.15 - 0.75 * r, 0.32, 1.15)[..., None]
    img = np.clip(base * shade, 0, 255)
    # diagonal stripes in the accent colour (thin, subtle) + one broad band
    stripe = ((xx + yy) // 26) % 2 == 0
    img = np.where(stripe[..., None], img * 0.93 + accent * 0.07, img)
    band = np.abs((xx - yy * 1.25) - W * 0.62) < 70
    img = np.where(band[..., None], img * 0.82 + accent * 0.18, img)
    bg = Image.fromarray(img.astype("uint8"), "RGB").convert("RGBA")
    # big faint crest on the right
    wm = logo.copy(); wm.thumbnail((720, 720))
    wm.putalpha(wm.getchannel("A").point(lambda v: int(v * 0.09)))
    wm = wm.rotate(-10, expand=True, resample=Image.BICUBIC)
    bg.alpha_composite(wm, (W - wm.width + 190, H - wm.height + 170))
    # the crest, big, with a soft shadow
    k = 440 / max(logo.size)
    big = logo.resize((round(logo.width * k), round(logo.height * k)), Image.LANCZOS)
    x, y = (W - big.width) // 2, (H - big.height) // 2
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sh.paste(Image.new("RGBA", big.size, (0, 0, 0, 170)), (x + 8, y + 14), big)
    bg.alpha_composite(sh.filter(ImageFilter.GaussianBlur(16)))
    # soft white halo around the crest: parts in the background's colour stay visible
    halo = Image.new("L", (W, H), 0)
    halo.paste(big.getchannel("A"), (x, y))
    halo = halo.filter(ImageFilter.MaxFilter(31)).filter(ImageFilter.GaussianBlur(12)).point(lambda v: int(v * 0.88))
    bg.alpha_composite(Image.merge("RGBA", (*[Image.new("L", (W, H), 255)] * 3, halo)))
    bg.alpha_composite(big, (x, y))
    bg.convert("RGB").save(path, "JPEG", quality=90)
    return path
