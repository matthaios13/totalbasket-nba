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


def crest_colour(url):
    """Hex of a crest's average opaque colour, for the game card's team side."""
    logo = _logo(url, 120)
    if not logo:
        return "1d1d1d"
    px = [p for p in logo.getdata() if p[3] > 200 and not (p[0] > 235 and p[1] > 235 and p[2] > 235)]
    if not px:
        return "1d1d1d"
    r, g, b = (sum(p[i] for p in px) // len(px) for i in range(3))
    return f"{r:02x}{g:02x}{b:02x}"


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


def game_card(path, away, home, label="NBA"):
    """away/home: dict(name, score, colour, winner, logo). Home team on the left, like the article's
    title «Γηπεδούχος – Φιλοξενούμενη» (user, 2026-10-28)."""
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    ch, ca = _darken(_rgb(home["colour"])), _darken(_rgb(away["colour"]))
    # diagonal split between the two team colours
    d.polygon([(0, 0), (W * 0.58, 0), (W * 0.42, H), (0, H)], fill=ch)
    d.polygon([(W * 0.58, 0), (W, 0), (W, H), (W * 0.42, H)], fill=ca)
    d.line([(W * 0.58, 0), (W * 0.42, H)], fill=ORANGE, width=8)

    for cx, t in ((W * 0.25, home), (W * 0.75, away)):
        _badge(img, _logo(t.get("logo"), 170), cx, 145, 110)
        name_f = _fit(d, t["name"], BLACK, 440, 50)
        d.text((cx, 295), t["name"], font=name_f, fill="white", anchor="mm")
        colour = "white" if t["winner"] else (200, 200, 200)
        d.text((cx, 425), str(t["score"]), font=_font(BLACK, 150), fill=colour, anchor="mm")
        if t["winner"]:
            d.rectangle([cx - 55, 520, cx + 55, 528], fill=ORANGE)
    _footer(d, f"{label}  •  ΤΕΛΙΚΟ")
    img.save(path, "JPEG", quality=88)
    return path


def news_card(path, title=None, colour=None, logo=None):
    """Team logo as a clean white wallpaper: big logo in the middle, faint logo pattern behind, no text.
    Without a logo it falls back to a plain NBA title card."""
    big = _logo(logo, 520)
    if not big:
        return _title_card(path, title or "", colour or "1d1d1d")
    # light badges (e.g. white crests) disappear on white: use a near-black wallpaper for them
    px = [p for p in big.getdata() if p[3] > 128]
    light = px and sum(0.299 * r + 0.587 * g + 0.114 * b for r, g, b, _ in px) / len(px) > 200
    img = Image.new("RGB", (W, H), (17, 17, 20) if light else "white")
    tile = _logo(logo, 90)
    faint = tile.copy()
    faint.putalpha(faint.getchannel("A").point(lambda a: a * (0.12 if light else 0.07)))
    for row, y in enumerate(range(-30, H, 130)):
        for x in range(-60 + (row % 2) * 90, W, 180):
            img.paste(faint, (x, y), faint)
    img.paste(big, ((W - big.width) // 2, (H - big.height) // 2), big)
    img.save(path, "JPEG", quality=90)
    return path


def _title_card(path, title, colour):
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


YUNET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "face_detection_yunet_2023mar.onnx")


def _face(photo):
    """(x, y, w, h) of the largest real face, or None.

    YuNet (OpenCV's neural face detector, model in models/) first: it ignores shoes, shirts and tiny faces in the
    crowd that the old Haar cascade took for faces (2026-10-29: photos of a torso / of legs went on articles).
    Only a confident (score >= 0.8), sizeable face (>= 8% of the height) counts. Haar is only the fallback
    when YuNet is unavailable. Needs opencv-python-headless < 5."""
    try:
        import cv2
        import numpy as np
    except Exception:            # OpenCV missing: fall back to the plain crop
        return None
    arr = np.array(photo)
    if os.path.exists(YUNET) and hasattr(cv2, "FaceDetectorYN"):
        try:
            bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            scale = min(1.0, 1280 / max(photo.size))       # big Commons originals: detect on a smaller copy
            if scale < 1:
                bgr = cv2.resize(bgr, (int(photo.width * scale), int(photo.height * scale)))
            h, w = bgr.shape[:2]
            det = cv2.FaceDetectorYN.create(YUNET, "", (w, h), 0.8, 0.3, 5000)
            _, faces = det.detect(bgr)
            faces = [] if faces is None else [f for f in faces if f[3] >= h * 0.08]
            if not faces:
                return None
            f = max(faces, key=lambda f: f[2] * f[3])
            return tuple(int(round(v / scale)) for v in f[:4])
        except Exception:
            pass
    try:
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        faces = det.detectMultiScale(gray, 1.1, 6, minSize=(max(60, photo.width // 16),) * 2)
    except Exception:
        return None
    if len(faces) == 0:
        return None
    return tuple(int(v) for v in max(faces, key=lambda f: f[2] * f[3]))


def face_share(photo):
    """How much of the 16:9 frame's height the face takes once the photo fills the whole width
    (None without a face). Under ~0.5 the whole head fits comfortably."""
    face = _face(photo)
    if not face:
        return None
    return face[3] * max(W / photo.width, H / photo.height) / H


def photo_card(path, photo):
    """Person photo filling the whole 16:9 frame (user, 2026-10-28: widescreen, never a narrow photo
    between blurred bars), cropped so the whole head stays visible: some room above the hair, face a bit
    above the middle."""
    from PIL import ImageEnhance, ImageFilter
    face = _face(photo)
    scale = max(W / photo.width, H / photo.height)
    if face and face[3] * scale > H * 0.75:
        # a very tight portrait: filling the width would cut the face, so it stays a little narrower
        # (face at 60% of the height) on a blurred, darkened copy of itself
        small = min(scale, H * 0.6 / face[3])
        fg = photo.resize((round(photo.width * small), round(photo.height * small)), Image.LANCZOS)
        fy = face[1] * small
        top = int(min(max(fy - face[3] * small * 0.3, 0), max(fg.height - H, 0)))
        fg = fg.crop((0, top, fg.width, min(top + H, fg.height)))
        bg = photo.resize((round(photo.width * scale) + 1, round(photo.height * scale) + 1), Image.LANCZOS)
        bg = bg.crop(((bg.width - W) // 2, (bg.height - H) // 2, (bg.width - W) // 2 + W, (bg.height - H) // 2 + H))
        out = ImageEnhance.Brightness(bg.filter(ImageFilter.GaussianBlur(30))).enhance(0.5)
        out.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
        out.save(path, "JPEG", quality=90)
        return path
    img = photo.resize((max(W, round(photo.width * scale)), max(H, round(photo.height * scale))), Image.LANCZOS)
    if face:
        fx, fy, fh = (face[0] + face[2] / 2) * scale, face[1] * scale, face[3] * scale
        left = fx - W / 2
        top = min(fy - fh * 0.55, fy + fh * 0.5 - H * 0.42)     # hair above the box; face above the middle
    else:                                   # no face found: portraits keep the top, landscapes the centre
        left, top = (img.width - W) / 2, (img.height - H) * 0.06 if photo.width >= photo.height else 0
    left = int(min(max(left, 0), img.width - W))
    top = int(min(max(top, 0), img.height - H))
    img.crop((left, top, left + W, top + H)).save(path, "JPEG", quality=90)
    return path


def league_card(path, label):
    """Fallback when there is no person photo or club badge: the league's name on a clean white card."""
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    f = _fit(d, label, BLACK, W - 240, 150)
    d.text((W / 2, H / 2 - 20), label, font=f, fill=(20, 20, 24), anchor="mm")
    tw = d.textlength(label, font=f)
    d.rectangle([W / 2 - tw / 2, H / 2 + 70, W / 2 + tw / 2, H / 2 + 82], fill=ORANGE)
    img.save(path, "JPEG", quality=90)
    return path
