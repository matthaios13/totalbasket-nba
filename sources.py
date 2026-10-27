"""News sources beyond ESPN: RSS feeds for the NBA (RealGM), the Italian, Spanish and French leagues and
EuroLeague / EuroCup (Sportando), plus club badges (official EuroLeague API, else TheSportsDB)."""
import email.utils
import hashlib
import html
import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"}

EUROPE_CATEGORY = 5    # "Πρωταθλήματα": the homepage "Rest of Europe" block, with a tab per league subcategory

# site category (dedupe list) / categories posted to / base tag / card label / badge source / how Claude
# should see the league. "official": EuroLeague API competition code (club crests).
LEAGUES = {
    "nba": {"category": 6, "categories": [6], "tag": 153, "label": "NBA", "sportsdb": None,
            "about": "NBA"},
    "lba": {"category": 131, "categories": [EUROPE_CATEGORY, 131], "tag": 208, "label": "LBA",
            "sportsdb": "Italian Lega Basket", "about": "ιταλικό πρωτάθλημα (Lega Basket Serie A / LBA)"},
    "acb": {"category": 158, "categories": [EUROPE_CATEGORY, 158], "tag": 159, "label": "LIGA ENDESA",
            "sportsdb": "Spanish Liga ACB", "about": "ισπανικό πρωτάθλημα (Liga Endesa / ACB)"},
    "lnb": {"category": 239, "categories": [EUROPE_CATEGORY, 239], "tag": 332, "label": "BETCLIC ÉLITE",
            "sportsdb": "French LNB", "about": "γαλλικό πρωτάθλημα (Betclic Élite)"},
    "el": {"category": 2, "categories": [2], "tag": 105, "label": "EUROLEAGUE", "sportsdb": None,
           "official": "E", "about": "EuroLeague"},
    "ec": {"category": 12, "categories": [12], "tag": 1233, "label": "EUROCUP", "sportsdb": None,
           "official": "U", "about": "EuroCup"},
}

# key -> feed. only_cats: keep items with an RSS category matching this regex.
# fetch_page: the feed has only a teaser, so read the article page for the full text.
FEEDS = [
    {"key": "realgm", "league": "nba", "lang": "en", "max_judge": 4,
     "url": "https://basketball.realgm.com/rss/wiretap/0/0.xml"},
    {"key": "sportando", "league": "lba", "lang": "it", "max_judge": 4,
     "url": "https://www.sportando.basketball/category/serie-a/feed/"},
    {"key": "encestando", "league": "acb", "lang": "es", "max_judge": 3, "fetch_page": True,
     "url": "https://www.encestando.es/feed/", "only_cats": r"^Liga ACB$"},
    {"key": "marca-acb", "league": "acb", "lang": "es", "max_judge": 3, "fetch_page": True,
     "url": "https://e00-marca.uecdn.es/rss/baloncesto/acb.xml"},
    {"key": "bebasket", "league": "lnb", "lang": "fr", "max_judge": 3,
     "url": "https://www.bebasket.fr/rss/feed.xml", "only_cats": r"(?i)betclic|^pro a$|^[ée]lite$"},
    # L'Equipe blocks its article pages, so only the 2-3 sentence teaser is available
    {"key": "lequipe", "league": "lnb", "lang": "fr", "max_judge": 3,
     "url": "https://dwh.lequipe.fr/api/edito/rss?path=/Basket/Betclic-elite/"},
    # euroleaguebasketball.net itself sits behind a bot checkpoint, so its news is not used
    {"key": "sportando-el", "league": "el", "lang": "it", "max_judge": 4,
     "url": "https://www.sportando.basketball/category/euroleague/feed/"},
    {"key": "sportando-ec", "league": "ec", "lang": "it", "max_judge": 4,
     "url": "https://www.sportando.basketball/category/eurocup/feed/"},
]

NS = {"content": "http://purl.org/rss/1.0/modules/content/"}


def _fetch(url, timeout=30):
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError:
            raise
        except OSError:
            if i == 2:
                raise
            time.sleep(5 * (i + 1))


def _text(s):
    s = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", s or "", flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>|</p>", "\n", s)
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", s)).strip()


def page_text(url):
    """Readable paragraphs of an article page (scripts, menus and short fragments dropped)."""
    raw = _fetch(url).decode("utf-8", "ignore")
    raw = re.sub(r"<(script|style|noscript|nav|header|footer|aside)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    body = re.search(r"<article[^>]*>(.*?)</article>", raw, re.S | re.I)
    paras = [_text(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", body.group(1) if body else raw, re.S | re.I)]
    paras = [p for p in paras if len(p) > 50 and "{" not in p and "function(" not in p]
    return "\n\n".join(paras)[:9000]


def items(feed):
    """New-style news items of one feed: dicts with id, title, link, published, summary, story, cats."""
    root = ET.fromstring(_fetch(feed["url"]))
    for it in root.iter("item"):
        title = _text(it.findtext("title"))
        link = (it.findtext("link") or "").strip()
        cats = [_text(c.text) for c in it.findall("category") if c.text]
        if feed.get("only_cats") and not any(re.search(feed["only_cats"], c) for c in cats):
            continue
        guid = (it.findtext("guid") or link or title).strip()
        try:
            published = email.utils.parsedate_to_datetime(it.findtext("pubDate"))
        except (TypeError, ValueError):
            published = None
        yield {
            "id": feed["key"] + ":" + hashlib.sha1(guid.encode()).hexdigest()[:16],
            "title": title,
            "link": link,
            "published": published,
            "summary": _text(it.findtext("description"))[:1500],
            "story": _text(it.findtext("content:encoded", namespaces=NS))[:9000],
            "cats": cats,
        }


def full_story(feed, item):
    story = item["story"]
    if feed.get("fetch_page") and len(story) < 600 and item["link"]:
        try:
            story = page_text(item["link"]) or story
        except Exception:
            pass
    return story or item["summary"]


# ---------------------------------------------------------------- team badges (European clubs)

_badges = {}          # "league|team" -> badge URL or "" (not found); persisted in state.json by bot.py


_crests = {}


def _words(s):
    return set(re.findall(r"[a-z0-9]{3,}", unicodedata.normalize("NFKD", s.lower()).encode("ascii", "ignore").decode()))


def official_crest(comp, team):
    """Club crest from the EuroLeague API (comp "E" or "U"): best word overlap with the club's name."""
    if comp not in _crests:
        today = time.gmtime()
        season = today.tm_year if today.tm_mon >= 7 else today.tm_year - 1
        try:
            url = f"https://api-live.euroleague.net/v2/competitions/{comp}/seasons/{comp}{season}/clubs"
            data = json.loads(_fetch(url, timeout=20))
            clubs = data.get("data", data) if isinstance(data, dict) else data
            _crests[comp] = [(_words(c.get("name", "")), (c.get("images") or {}).get("crest")) for c in clubs]
        except Exception:
            return None
    want = _words(team) - {"basket", "basketball", "club", "bc", "kk"}
    scored = sorted(((len(want & w), url) for w, url in _crests[comp] if url), reverse=True)
    if not scored or not scored[0][0]:
        return None
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        return None        # ambiguous (e.g. "Bayern Monaco": Bayern Munich or AS Monaco?) -> no crest
    return scored[0][1]


def badge(league, team):
    """PNG badge URL of a club in that league, or None. Women's/youth sides are ignored."""
    if LEAGUES[league].get("official"):
        return official_crest(LEAGUES[league]["official"], team or "")
    lg = LEAGUES[league]["sportsdb"]
    team = (team or "").strip()
    if not lg or not team:
        return None
    key = f"{league}|{team.lower()}"
    if key in _badges:
        return _badges[key] or None
    base = re.sub(r"(?i)\b(basketball|basket|baloncesto|pallacanestro|club)\b", "", team).strip(" -")
    names = list(dict.fromkeys([team, base, base + " Basket", base + " Baloncesto", base + " Basketball"]))
    for name in names:
        try:
            url = "https://www.thesportsdb.com/api/v1/json/123/searchteams.php?t=" + urllib.parse.quote(name)
            teams = json.loads(_fetch(url, timeout=20)).get("teams") or []
        except Exception:
            return None                    # rate limit / outage: don't remember a miss, try next run
        hit = next((t["strBadge"] for t in teams
                    if t.get("strSport") == "Basketball" and t.get("strLeague") == lg and t.get("strBadge")
                    and not re.search(r"wom|fémin|femen|femmin", t.get("strTeam", ""), re.I)), None)
        if hit:
            _badges[key] = hit
            return hit
        time.sleep(2)                      # free API: stay well under its rate limit
    _badges[key] = ""
    return None
