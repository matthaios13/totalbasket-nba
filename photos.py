"""Free-licence photos of people from Wikipedia / Wikimedia Commons.

Only images whose licence allows reuse (Creative Commons or public domain) are returned,
together with the credit line the licence requires.
"""
import html
import io
import json
import re
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


def find(name):
    """-> (PIL image, credit text) or None."""
    pages = _get({"action": "query", "generator": "search", "gsrsearch": f"{name} NBA basketball",
                  "gsrlimit": 1, "prop": "pageimages", "piprop": "name"}).get("query", {}).get("pages", {})
    page = next(iter(pages.values()), None)
    if not page or not page.get("pageimage"):
        return None
    # the search must have landed on this person's page, not on a team or another player
    surname = name.split()[-1].lower()
    if surname not in page["title"].lower():
        return None
    info = _get({"action": "query", "titles": "File:" + page["pageimage"], "prop": "imageinfo",
                 "iiprop": "url|extmetadata", "iiurlwidth": 1600})
    ii = (next(iter(info["query"]["pages"].values())).get("imageinfo") or [{}])[0]
    meta = ii.get("extmetadata", {})
    licence = _clean(meta.get("LicenseShortName", {}).get("value"))
    if not FREE.match(licence) or not ii.get("thumburl"):
        return None
    artist = _clean(meta.get("Artist", {}).get("value")) or "Wikimedia Commons"
    with urllib.request.urlopen(urllib.request.Request(ii["thumburl"], headers=UA), timeout=30) as r:
        img = Image.open(io.BytesIO(r.read())).convert("RGB")
    return img, f"{artist} / {licence} / Wikimedia Commons"
