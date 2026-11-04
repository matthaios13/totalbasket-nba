"""App-only images for new articles of totalbasket.gr and bala24.gr (hourly, after the bot).

The apps show only photos with a known, lawful source (mu-plugin bala-app-safe.php on both sites). When a new
article's own photo isn't one of them, this job gives the APP (post meta _bala_app_img) instead:
  1. the real face of the ONE person the article is about: Claude Haiku reads title + text and names him/her by the
     exact English Wikipedia title (namesakes: Armani's Devin Booker is not the NBA's), and only the portrait of that
     Wikipedia page is used (never search results), with a clearly visible face;
  2. else the crest of the main club (also for official club announcements);
  3. else nothing: the app shows the site's logo card. Never a wrong face (user, 2026-11-03).
The site's own photo is never changed.

Env: WP_USER / WP_APP_PASSWORD (totalbasket), BALA_WP_USER / BALA_WP_APP_PASSWORD (bala24, optional),
CLAUDE_CODE_OAUTH_TOKEN. APP_IMG_DRY=1 decides and writes out/app-images/ without touching the sites.
"""
import base64
import datetime as dt
import html
import io
import json
import os
import re
import sys
import tempfile
import urllib.parse
import urllib.request

from PIL import Image

import bot
import card
import crestcard
import photos
from bot import AuthError, QuotaError, UA, log, now, retry, strip_html

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "app_images_state.json")
DRY = os.environ.get("APP_IMG_DRY") == "1"
MAX_PER_SITE = int(os.environ.get("APP_IMG_MAX", "25"))
HOURS = int(os.environ.get("APP_IMG_HOURS", "36"))
# APP_IMG_REDO=1: look again at articles that show only a logo / crest card, for a face (user, 2026-11-04)
REDO = os.environ.get("APP_IMG_REDO") == "1"
CRESTS = json.load(open(os.path.join(HERE, "crests.json")))

SITES = {
    # own_uploaders: users who make their own images (Marinos Lavdas, user 15: AI graphics for his blog,
    # user 2026-11-04) -> what they upload to their posts is marked «Πηγή: own» and shown in the apps
    "totalbasket": {"url": "https://totalbasket.gr", "user": "WP_USER", "pw": "WP_APP_PASSWORD", "sport": "basketball",
                    "own_uploaders": {15}},
    "bala24": {"url": "https://bala24.gr", "user": "BALA_WP_USER", "pw": "BALA_WP_APP_PASSWORD", "sport": "football",
               "own_uploaders": set()},
}


def wp(site, method, path, data=None, headers=None, raw=None):
    s = SITES[site]
    auth = base64.b64encode(f'{os.environ[s["user"]]}:{os.environ[s["pw"]]}'.encode()).decode()
    h = {"Authorization": "Basic " + auth, **UA, **(headers or {})}
    body = raw
    if data is not None:
        body = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(f'{s["url"]}/wp-json/wp/v2/{path}', data=body, headers=h, method=method)

    def go():
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    try:
        return retry(go)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise AuthError(f"{site}: WordPress rejected the application password ({method} {path})")
        raise RuntimeError(f"{site} {method} {path}: {e.code} {e.read()[:300]!r}")


# ---------------------------------------------------------------- who is the article about

ASK = """Διαβάζεις ελληνικό αθλητικό άρθρο ({sport}) και διαλέγεις φωτογραφία για την εφαρμογή.
Απαντάς ΜΟΝΟ JSON: {{"person": ..., "team": ..., "announcement": true/false}}
- "person": ο ΕΝΑΣ πραγματικός άνθρωπος (παίκτης, προπονητής, πρόεδρος) που πρωταγωνιστεί στο άρθρο, γραμμένος
  ακριβώς όπως ο τίτλος του άρθρου του στην αγγλική Wikipedia (π.χ. "Georgios Bartzokas", "Iñaki Peña",
  "Marko Nikolić (football manager)"). Αν το άρθρο είναι για ένα πρόσωπο, αυτό. Αν είναι για ομάδα ή αγώνα, το πρόσωπο
  που είναι στον τίτλο ή πρωταγωνιστεί στο κείμενο: ο προπονητής που κάνει τις δηλώσεις, ο παίκτης που ξεχώρισε ή
  που αφορά η είδηση (τραυματισμός, μεταγραφή, κλήση). Πρόσεξε τους συνονόματους: ο σέντερ της Αρμάνι Ντέβιν Μπούκερ
  ΔΕΝ είναι ο Devin Booker του NBA -> "Devin Booker (basketball, born 1991)". Ποτέ κάποιον που απλώς αναφέρεται
  (π.χ. σε σύγκριση). null αν δεν υπάρχει τέτοιο πρόσωπο ή αν δεν είσαι σίγουρος ποιος ακριβώς είναι.
- "team": η κύρια ομάδα του άρθρου στα αγγλικά (π.χ. "Olympiacos B.C.", "PAOK FC", "Real Madrid CF") ή null.
- "announcement": true αν είναι επίσημη ανακοίνωση ομάδας (ΠΑΕ/ΚΑΕ, μεταγραφή, ανανέωση, εισιτήρια, δελτίο τύπου).

ΑΡΘΡΟ:
{data}"""


def decide(site, post):
    text = strip_html(post["content"]["raw"] or post["content"].get("rendered", ""))[:1800]
    data = json.dumps({"title": html.unescape(post["title"]["raw"]), "text": text}, ensure_ascii=False, indent=1)
    return bot._claude(ASK.format(sport=SITES[site]["sport"], data=data), bot.TRIAGE_MODEL)


# ---------------------------------------------------------------- photos

def _football_page(name):
    for title in (name, f"{name} (footballer)", f"{name} (football manager)"):
        pages = photos._get({"action": "query", "titles": title, "redirects": 1, "prop": "pageimages|categories",
                             "piprop": "name", "clcategories": "Category:Living people", "cllimit": 5}
                            ).get("query", {}).get("pages", {})
        page = next(iter(pages.values()), {})
        if page.get("pageimage") and page.get("categories"):
            return page
    return None


SPORT_WORDS = {"basketball": re.compile(r"basketball", re.I), "football": re.compile(r"football|soccer", re.I)}


def clear_face(img):
    """The photo, or a crop of its upper part, where the face is clear and fits a 16:9 card (full-body shots
    have a face too small to see: Patty Mills, 2026-11-04). None if there is no such crop."""
    w, h = img.size
    for box in ((0, 0, w, h), (0, 0, w, int(h * 0.6)), (int(w * 0.15), 0, int(w * 0.85), int(h * 0.45)),
                (int(w * 0.25), 0, int(w * 0.75), int(h * 0.32))):
        crop = img.crop(box)
        if crop.width < 600:
            break
        share = card.face_share(crop)
        if share is None or share > 0.6:
            continue
        if share >= 0.22:                 # the face reads well on a phone (smaller ones look like a crowd shot)
            return crop
    return None


def strict_photo(name, site):
    """A photo of the person, of the right sport, with a clear face: the portrait of his/her own English Wikipedia
    page first, else a photo on that page or on Commons whose file name has the full name and only this person
    (photos.find). The page must exist and be of the right sport, so a namesake never gets in."""
    page = (_football_page if site == "bala24" else photos._page)(name) or \
        (photos._page if site == "bala24" else _football_page)(name)
    if not page or not page.get("pageimage"):
        return None
    if photos._plain(re.sub(r"\s*\(.*?\)$", "", name)) not in photos._plain(page.get("title", "")):
        return None
    desc = photos._get({"action": "query", "pageids": page["pageid"], "prop": "pageprops|categories", "cllimit": 50})
    p = next(iter(desc["query"]["pages"].values()), {})
    about = (p.get("pageprops", {}).get("wikibase-shortdesc", "") + " " +
             " ".join(c["title"] for c in p.get("categories", [])))
    if not SPORT_WORDS[SITES[site]["sport"]].search(about) and not re.search(r"coach|manager|president|executive", about, re.I):
        return None
    # the app only needs the right, clear face (user, 2026-11-04: more faces): up to 10 years old, from 600px
    photos.MAX_AGE, photos.MIN_WIDTH = 10, 600
    info = photos._get({"action": "query", "titles": "File:" + page["pageimage"], "prop": "imageinfo",
                        "iiprop": "url|size|extmetadata", "iiurlwidth": 1600})
    cands = photos._candidates(list(info["query"]["pages"].values()), lambda t: True)
    if cands:
        _, url, credit = cands[0]
        with urllib.request.urlopen(urllib.request.Request(url, headers=photos.UA), timeout=40) as r:
            img = clear_face(Image.open(io.BytesIO(r.read())).convert("RGB"))
        if img:
            return img, credit
    # more photos of the same person (user, 2026-11-04: more faces, fewer logos); not for namesakes
    # ("Devin Booker (basketball, born 1991)"): files named after them may show the other one
    if "(" in (page.get("title") or ""):
        return None
    found = photos.find(re.sub(r"\s*\(.*?\)$", "", page.get("title") or name), accept=lambda im: clear_face(im) is not None)
    if found and clear_face(found[0]):
        return clear_face(found[0]), found[1]
    return None


def _norm(s):
    s = photos._plain(re.sub(r"\(.*?\)", "", s or ""))
    s = re.sub(r"\b(f\.?c\.?|b\.?c\.?|bc|cf|s\.?k\.?|k\.?k\.?|sc|ac|fk|pfk|bk|basket(ball)?|baloncesto|football club)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def crest_url(team, site):
    table = {**CRESTS.get("any", {}), **CRESTS.get(site, {})}
    if team in table:
        return table[team]
    by_norm = {_norm(k): v for k, v in table.items() if v}
    if _norm(team) in by_norm:
        return by_norm[_norm(team)]
    q = urllib.parse.quote(_norm(team))
    try:
        if site == "bala24":
            for g in bot.get_json(f"https://www.fotmob.com/api/data/search/suggest?lang=en&term={q}") or []:
                for s in g.get("suggestions", []):
                    if s.get("type") == "team":
                        return f"https://images.fotmob.com/image_resources/logo/teamlogo/{s['id']}.png"
        want = "Soccer" if site == "bala24" else "Basketball"
        for t in (bot.get_json(f"https://www.thesportsdb.com/api/v1/json/123/searchteams.php?t={q}") or {}).get("teams") or []:
            if t.get("strSport") == want and t.get("strBadge"):
                return t["strBadge"]
    except Exception as ex:
        log("  crest search failed:", team, ex)
    return None


def crest_card(team, site):
    """Crest on the club's colours (user, 2026-11-03: never a bare crest on white)."""
    url = crest_url(team, site)
    if not url:
        return None
    return crestcard.crest_card(tempfile.mktemp(suffix=".jpg"), url, team)


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", photos._plain(s)).strip("-")[:60]


# ---------------------------------------------------------------- run

def load_state():
    try:
        return json.load(open(STATE_FILE))
    except FileNotFoundError:
        return {"done": {}, "media": {}}


def save_state(st):
    cutoff = (now() - dt.timedelta(days=7)).isoformat()
    st["done"] = {k: v for k, v in st["done"].items() if v.get("at", "") >= cutoff}
    json.dump(st, open(STATE_FILE, "w"), ensure_ascii=False, indent=1, sort_keys=True)


def upload(site, path, name, subject, credit, st):
    key = f"{site}:{name}"
    if key in st["media"]:
        return st["media"][key]
    with open(path, "rb") as f:
        m = wp(site, "POST", "media", raw=f.read(), headers={
            "Content-Type": "image/jpeg", "Content-Disposition": f'attachment; filename="{name}"'})
    meta = {"alt_text": subject, "title": subject}
    if credit:
        meta["caption"] = "Φωτογραφία: " + credit
    wp(site, "POST", f"media/{m['id']}", meta)
    st["media"][key] = m["id"]
    return m["id"]


def mark_own_images(site, post):
    """Featured + inline images that an own_uploaders user uploaded -> «Πηγή: own». True if the featured one was."""
    ids = [post.get("featured_media")] + [int(i) for i in re.findall(r"wp-image-(\d+)", post["content"]["raw"] or "")]
    ids = [i for i in dict.fromkeys(ids) if i]
    if not ids:
        return False
    media = wp(site, "GET", f"media?include={','.join(map(str, ids))}&per_page=100&context=edit&_fields=id,author,meta")
    own = [m["id"] for m in media if m.get("author") in SITES[site]["own_uploaders"]]
    for mid in own:
        log(f"{site} {post['id']}: own image {mid}")
        if not DRY:
            wp(site, "POST", f"media/{mid}", {"meta": {"_bala_src": "own"}})
    return post.get("featured_media") in own


def run_site(site, st):
    s = SITES[site]
    if not (os.environ.get(s["user"]) and os.environ.get(s["pw"])):
        log(f"{site}: no application password, skipped")
        return
    after = (now() - dt.timedelta(hours=HOURS)).strftime("%Y-%m-%dT%H:%M:%S")
    posts = []
    for page in range(1, (5 if REDO else 1) + 1):
        batch = wp(site, "GET", f"posts?per_page=50&page={page}&after={after}&context=edit"
                                "&_fields=id,title,content,featured_media,bala_app_safe")
        posts += batch
        if len(batch) < 50:
            break
    n = 0
    for post in posts:
        key = f"{site}:{post['id']}"
        safe = post.get("bala_app_safe") or {}
        # a plain logo / crest card (site photo or ours) is only a stand-in: a clear face of the right person wins
        has_face = (safe.get("featured") and not safe.get("featured_card")) or (safe.get("app_img") and not safe.get("app_img_card"))
        only_card = not has_face and (safe.get("featured") or safe.get("app_img"))
        if has_face or (only_card and "featured_card" not in safe):      # (old plugin: no card info)
            continue
        if key in st["done"] and not (REDO and only_card and st["done"][key].get("pick") != "face"):
            continue
        if s["own_uploaders"] and mark_own_images(site, post):
            st["done"][key] = {"at": now().isoformat(), "pick": "own images"}
            continue
        if n >= MAX_PER_SITE:
            break
        n += 1
        d = decide(site, post)
        pick = None
        if d.get("person"):
            ph = strict_photo(d["person"], site)
            if ph:
                path = card.photo_card(tempfile.mktemp(suffix=".jpg"), ph[0])
                pick = (path, f"{site}-p-{slug(d['person'])}.jpg", d["person"], ph[1])
        if not pick and only_card:           # it already has a card: a second card adds nothing
            log(f"{site} {post['id']} -> {d} -> no face, keeps its card")
            st["done"][key] = {"at": now().isoformat(), "pick": None}
            continue
        if not pick and d.get("team"):
            path = crest_card(d["team"], site)
            if path:
                pick = (path, f"{site}-c-{slug(d['team'])}-v2.jpg", d["team"], None)
        title = html.unescape(post["title"]["raw"])[:70]
        log(f"{site} {post['id']} {title} -> {d} -> {pick[2] if pick else 'site logo'}")
        st["done"][key] = {"at": now().isoformat(), "pick": ("face" if "-p-" in pick[1] else pick[2]) if pick else None}
        if not pick:
            continue
        if DRY:
            os.makedirs(os.path.join(HERE, "out", "app-images"), exist_ok=True)
            os.replace(pick[0], os.path.join(HERE, "out", "app-images", f"{post['id']}-{pick[1]}"))
            continue
        mid = upload(site, pick[0], pick[1], pick[2], pick[3], st)
        wp(site, "POST", f"posts/{post['id']}", {"meta": {"_bala_app_img": mid}})


def main():
    st = load_state()
    try:
        for site in SITES:
            run_site(site, st)
    except QuotaError as ex:
        log("PAUSED:", ex)
    finally:
        if not DRY:
            save_state(st)


if __name__ == "__main__":
    main()
