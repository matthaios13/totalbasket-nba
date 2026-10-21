"""Featured images we draw ourselves (no third-party photos -> no copyright risk)."""
import io
import os
import textwrap
import urllib.request

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 675
HERE = os.path.dirname(os.path.abspath(__file__))
BLACK = os.path.join(HERE, "fonts", "NotoSans-Black.ttf")
BOLD = os.path.join(HERE, "fonts", "NotoSans-Bold.ttf")
ORANGE = (245, 132, 38)


def _rgb(hex_):
    hex_ = hex_.lstrip("#")
    return tuple(int(hex_[i:i + 2], 16) for i in (0, 2, 4))


def _darken(c, f=0.55):
    return tuple(int(v * f) for v in c)


def _font(path, size):
    return ImageFont.truetype(path, size)


def _fit(draw, text, path, max_w, start):
    size = start
    while size > 20:
        f = _font(path, size)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 4
    return _font(path, size)


_logos = {}


def _logo(url, size):
    """Team logo (transparent PNG) scaled to fit size x size, or None if it can't be fetched."""
    if not url:
        return None
    if url not in _logos:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                _logos[url] = Image.open(io.BytesIO(r.read())).convert("RGBA")
        except Exception:
            _logos[url] = None
    img = _logos[url]
    if img is None:
        return None
    img = img.copy()
    img.thumbnail((size, size), Image.LANCZOS)
    return img


def _badge(img, logo, cx, cy, r):
    """White disc with the logo centred on it, so every logo reads on any background."""
    d = ImageDraw.Draw(img)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill="white")
    if logo:
        img.paste(logo, (int(cx - logo.width / 2), int(cy - logo.height / 2)), logo)


def _footer(d, label):
    d.rectangle([0, H - 70, W, H], fill=(12, 12, 14))
    d.rectangle([0, H - 74, W, H - 70], fill=ORANGE)
    d.text((40, H - 35), label, font=_font(BLACK, 30), fill=ORANGE, anchor="lm")
    d.text((W - 40, H - 35), "totalbasket.gr", font=_font(BOLD, 28), fill="white", anchor="rm")


def game_card(path, away, home):
    """away/home: dict(name, score, colour, winner, logo)."""
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    ca, ch = _darken(_rgb(away["colour"])), _darken(_rgb(home["colour"]))
    # diagonal split between the two team colours
    d.polygon([(0, 0), (W * 0.58, 0), (W * 0.42, H), (0, H)], fill=ca)
    d.polygon([(W * 0.58, 0), (W, 0), (W, H), (W * 0.42, H)], fill=ch)
    d.line([(W * 0.58, 0), (W * 0.42, H)], fill=ORANGE, width=8)

    for side, t in (("away", away), ("home", home)):
        cx = W * 0.25 if side == "away" else W * 0.75
        _badge(img, _logo(t.get("logo"), 170), cx, 145, 110)
        name_f = _fit(d, t["name"], BLACK, 440, 50)
        d.text((cx, 295), t["name"], font=name_f, fill="white", anchor="mm")
        colour = "white" if t["winner"] else (200, 200, 200)
        d.text((cx, 425), str(t["score"]), font=_font(BLACK, 150), fill=colour, anchor="mm")
        if t["winner"]:
            d.rectangle([cx - 55, 520, cx + 55, 528], fill=ORANGE)
    _footer(d, "NBA  •  ΤΕΛΙΚΟ")
    img.save(path, "JPEG", quality=88)
    return path


def news_card(path, title, colour="1d1d1d", logo=None):
    img = Image.new("RGB", (W, H), _darken(_rgb(colour), 0.45))
    d = ImageDraw.Draw(img)
    logo_img = _logo(logo, 250)
    if logo_img:
        _badge(img, logo_img, W - 270, 300, 165)
    d.rectangle([40, 60, 52, 340], fill=ORANGE)
    d.text((80, 90), "NBA", font=_font(BLACK, 64), fill=ORANGE, anchor="lm")
    lines = textwrap.wrap(title, 22 if logo_img else 30)[:5]
    f = _font(BLACK, 58 if len(lines) <= 3 else 48)
    y = 175
    for line in lines:
        d.text((80, y), line, font=f, fill="white", anchor="lm")
        y += 72 if len(lines) > 3 else 78
    _footer(d, "NBA  •  ΕΙΔΗΣΕΙΣ")
    img.save(path, "JPEG", quality=88)
    return path


def photo_card(path, photo):
    """Person photo, uncropped, over a blurred and darkened copy of itself (works for portrait shots)."""
    from PIL import ImageFilter, ImageEnhance
    bg = photo.copy()
    scale = max(W / bg.width, H / bg.height)
    bg = bg.resize((int(bg.width * scale) + 1, int(bg.height * scale) + 1), Image.LANCZOS)
    bg = bg.crop(((bg.width - W) // 2, (bg.height - H) // 2, (bg.width - W) // 2 + W, (bg.height - H) // 2 + H))
    bg = ImageEnhance.Brightness(bg.filter(ImageFilter.GaussianBlur(28))).enhance(0.55)
    fg = photo.copy()
    fg.thumbnail((W, H), Image.LANCZOS)
    if fg.height < H and fg.width < W:          # small originals: grow to full height
        s = H / fg.height
        fg = fg.resize((int(fg.width * s), H), Image.LANCZOS)
        if fg.width > W:
            fg = fg.crop(((fg.width - W) // 2, 0, (fg.width - W) // 2 + W, H))
    bg.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
    d = ImageDraw.Draw(bg)
    d.rectangle([0, H - 6, W, H], fill=ORANGE)
    bg.save(path, "JPEG", quality=88)
    return path
