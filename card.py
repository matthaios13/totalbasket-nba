"""Featured images we draw ourselves (no third-party photos -> no copyright risk)."""
import os
import textwrap

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


def _footer(d, label):
    d.rectangle([0, H - 70, W, H], fill=(12, 12, 14))
    d.rectangle([0, H - 74, W, H - 70], fill=ORANGE)
    d.text((40, H - 35), label, font=_font(BLACK, 30), fill=ORANGE, anchor="lm")
    d.text((W - 40, H - 35), "totalbasket.gr", font=_font(BOLD, 28), fill="white", anchor="rm")


def game_card(path, away, home):
    """away/home: dict(name, score, colour, winner)."""
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    ca, ch = _darken(_rgb(away["colour"])), _darken(_rgb(home["colour"]))
    # diagonal split between the two team colours
    d.polygon([(0, 0), (W * 0.58, 0), (W * 0.42, H), (0, H)], fill=ca)
    d.polygon([(W * 0.58, 0), (W, 0), (W, H), (W * 0.42, H)], fill=ch)
    d.line([(W * 0.58, 0), (W * 0.42, H)], fill=ORANGE, width=8)

    for side, t in (("away", away), ("home", home)):
        cx = W * 0.25 if side == "away" else W * 0.75
        name_f = _fit(d, t["name"], BLACK, 470, 76)
        d.text((cx, 215), t["name"], font=name_f, fill="white", anchor="mm")
        colour = "white" if t["winner"] else (200, 200, 200)
        d.text((cx, 380), str(t["score"]), font=_font(BLACK, 190), fill=colour, anchor="mm")
        if t["winner"]:
            d.rectangle([cx - 60, 495, cx + 60, 503], fill=ORANGE)
    _footer(d, "NBA  •  ΤΕΛΙΚΟ")
    img.save(path, "JPEG", quality=88)
    return path


def news_card(path, title, colour="1d1d1d"):
    img = Image.new("RGB", (W, H), _darken(_rgb(colour), 0.45))
    d = ImageDraw.Draw(img)
    d.rectangle([40, 60, 52, 340], fill=ORANGE)
    d.text((80, 90), "NBA", font=_font(BLACK, 64), fill=ORANGE, anchor="lm")
    lines = textwrap.wrap(title, 30)[:4]
    f = _font(BLACK, 58 if len(lines) <= 3 else 50)
    y = 175
    for line in lines:
        d.text((80, y), line, font=f, fill="white", anchor="lm")
        y += 78
    _footer(d, "NBA  •  ΕΙΔΗΣΕΙΣ")
    img.save(path, "JPEG", quality=88)
    return path
