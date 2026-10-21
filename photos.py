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
    pages = _get({"action": "query", "generator": "search", "gsrsearch": f'"{name}" NBA basketball',
                  "gsrlimit": 3, "prop": "pageimages", "piprop": "name"}).get("query", {}).get("pages", {})
    for page in sorted(pages.values(), key=lambda p: p.get("index", 0)):
        if name.lower() in page["title"].lower() and page.get("pageimage"):
            return page
    return None


def find(name):
    """-> (PIL image, credit text) or None."""
    page = _page(name)
    if not page:
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
