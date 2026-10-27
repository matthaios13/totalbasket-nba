"""Free-licence photos of people from Wikipedia / Wikimedia Commons.

Only images whose licence allows reuse (Creative Commons or public domain) are returned,
together with the credit line the licence requires.
"""
import html
import io
import json
import re
import unicodedata
import urllib.parse
import urllib.request

from PIL import Image

API = "https://en.wikipedia.org/w/api.php"
UA = {"User-Agent": "totalbasket-nba-bot/1.0 (https://totalbasket.gr)"}
FREE = re.compile(r"^(CC[ -]?(BY|0|Zero)|Public domain|PD)", re.I)


def _get(params):
    url = API + "?" + urllib.parse.urlencode({"format": "json", **params})
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
        return json.load(r)


def _clean(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def _page(name):
    """The person's own Wikipedia page: exact title first ("LeBron James", not "Bronny James"),
    then "<name> (basketball)", then a search whose result title must contain the full name."""
    for title in (name, f"{name} (basketball)"):
        pages = _get({"action": "query", "titles": title, "redirects": 1,
                      "prop": "pageimages|categories", "piprop": "name", "clcategories":
                      "Category:Living people", "cllimit": 5}).get("query", {}).get("pages", {})
        page = next(iter(pages.values()), {})
        if page.get("pageimage") and page.get("categories"):     # living person with a picture
            return page
    pages = _get({"action": "query", "generator": "search", "gsrsearch": f'"{name}" basketball',
                  "gsrlimit": 3, "prop": "pageimages", "piprop": "name"}).get("query", {}).get("pages", {})
    for page in sorted(pages.values(), key=lambda p: p.get("index", 0)):
        if name.lower() in page["title"].lower() and page.get("pageimage"):
            return page
    return None


def _plain(s):
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if not unicodedata.combining(c))


def _year(meta, title):
    m = re.search(r"(19|20)\d\d", _clean(meta.get("DateTimeOriginal", {}).get("value")) + " " + title)
    return int(m.group(0)) if m else 0


def find(name):
    """-> (PIL image, credit text) or None.

    Prefers the infobox portrait; when that one isn't free, falls back to the newest free JPEG on the
    page that shows only this person (surname in the file name, no "X and Y" / "vs" shots)."""
    page = _page(name)
    if not page:
        return None
    surname = _plain(name.split()[-1])
    files = _get({"action": "query", "pageids": page["pageid"], "prop": "images", "imlimit": 50})
    titles = ["File:" + page["pageimage"]] + [
        i["title"] for p in files["query"]["pages"].values() for i in p.get("images", [])
        if i["title"].lower().endswith((".jpg", ".jpeg")) and surname in _plain(i["title"])
        and not re.search(r"(?i)\b(and|vs\.?|with|&)\b", i["title"])]
    titles = list(dict.fromkeys(titles))[:20]
    info = _get({"action": "query", "titles": "|".join(titles), "prop": "imageinfo",
                 "iiprop": "url|size|extmetadata", "iiurlwidth": 1600})
    best = None
    for p in info["query"]["pages"].values():
        ii = (p.get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata", {})
        licence = _clean(meta.get("LicenseShortName", {}).get("value"))
        if not FREE.match(licence) or not ii.get("thumburl") or ii.get("width", 0) < 700:
            continue
        # the infobox portrait is surely this person; other photos on the page are often wide game
        # scenes where the player is small or among others, so they are only a fallback
        infobox = p["title"].replace(" ", "_") == "File:" + page["pageimage"]
        score = (infobox, _year(meta, p["title"]), ii["width"] * ii["height"])
        if not best or score > best[0]:
            artist = _clean(meta.get("Artist", {}).get("value")) or "Wikimedia Commons"
            best = (score, ii["thumburl"], f"{artist} / {licence} / Wikimedia Commons")
    if not best:
        return None
    with urllib.request.urlopen(urllib.request.Request(best[1], headers=UA), timeout=30) as r:
        img = Image.open(io.BytesIO(r.read())).convert("RGB")
    return img, best[2]


COMMONS = "https://commons.wikimedia.org/w/api.php"


def team_photo(team, context="Euroleague", min_year=None):
    """Newest free-licence Commons photo of a club (e.g. "Olympiacos BC Euroleague 20260106.jpg"):
    the file name must contain the club's name, the photo must be from the last two seasons.
    -> (PIL image, credit) or None."""
    import time as _t
    min_year = min_year or _t.gmtime().tm_year - 2
    words = [w for w in re.findall(r"[a-z]{4,}", _plain(team)) if w not in ("basket", "basketball", "club")]
    if not words:
        return None
    q = urllib.parse.urlencode({"action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
                                "gsrlimit": 20, "gsrsearch": f"{team} {context} filetype:bitmap",
                                "prop": "imageinfo", "iiprop": "url|size|extmetadata", "iiurlwidth": 1600})
    with urllib.request.urlopen(urllib.request.Request(f"{COMMONS}?{q}", headers=UA), timeout=20) as r:
        pages = json.load(r).get("query", {}).get("pages", {}).values()
    best = None
    for p in pages:
        ii = (p.get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata", {})
        licence = _clean(meta.get("LicenseShortName", {}).get("value"))
        year = _year(meta, p["title"])
        if not FREE.match(licence) or ii.get("width", 0) < 1200 or year < min_year or not ii.get("thumburl"):
            continue
        if not any(w in _plain(p["title"]) for w in words):
            continue
        score = (year, p["title"])
        if not best or score > best[0]:
            artist = _clean(meta.get("Artist", {}).get("value")) or "Wikimedia Commons"
            best = (score, ii["thumburl"], f"{artist} / {licence} / Wikimedia Commons")
    if not best:
        return None
    with urllib.request.urlopen(urllib.request.Request(best[1], headers=UA), timeout=30) as r:
        img = Image.open(io.BytesIO(r.read())).convert("RGB")
    return img, best[2]
