"""Photos the site's editors already uploaded (the site's own media library), reused only when they are
clearly about the same game / person:

* game: a post published AFTER tip-off whose title names one of the two teams, with an image also uploaded
  after tip-off -> a photo of this very game, never of an older one;
* news: a post of the last days whose title names the person (or team) the article is about.

The bot's own images (nba-…, el-game-… cards, Wikimedia photos) are never reused.
"""
import datetime as dt
import html
import json
import re
import unicodedata
import urllib.parse
import urllib.request

WP = "https://totalbasket.gr/wp-json/wp/v2"
UA = {"User-Agent": "Mozilla/5.0 (totalbasket-bot)"}
OURS = re.compile(r"/(?:(?:nba|lba|acb|lnb|el|ec)-(?:game|news)-|(?:totalbasket|bala24)-[pcs]-)")   # bot cards; replacement / app crest & face cards (03/11/2026)


def _get(path):
    with urllib.request.urlopen(urllib.request.Request(WP + path, headers=UA), timeout=30) as r:
        return json.load(r)


def _norm(s):
    s = unicodedata.normalize("NFD", html.unescape(s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _utc(s):
    return dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc)


def _find(words, since, media_since=None, until=None, need_all=False):
    """Newest editor post between `since` and `until` whose title contains one of `words` (all of them
    with need_all), with a usable featured image. -> media id or None."""
    seen = set()
    words = [w for w in words if w and len(w) >= 3]
    for word in words:
        q = urllib.parse.urlencode({"search": word, "after": since.strftime("%Y-%m-%dT%H:%M:%S"),
                                    "per_page": 10, "_fields": "id,date_gmt,title,featured_media"})
        try:
            posts = _get("/posts?" + q)
        except Exception:
            continue
        for p in sorted(posts, key=lambda p: p["date_gmt"], reverse=True):
            mid = p.get("featured_media")
            title = _norm(p["title"]["rendered"])
            if not mid or mid in seen or _utc(p["date_gmt"]) < since or (until and _utc(p["date_gmt"]) > until) \
                    or not all(_norm(w) in title for w in (words if need_all else [word])):
                continue
            seen.add(mid)
            try:
                m = _get(f"/media/{mid}?_fields=id,date_gmt,source_url,media_details")
            except Exception:
                continue
            if OURS.search(m.get("source_url", "")) or (m.get("media_details") or {}).get("width", 0) < 600:
                continue
            if media_since and _utc(m["date_gmt"]) < media_since:
                continue                    # an older photo re-used by the editor: maybe another game
            return mid
    return None


def game_photo(team_names, tipoff):
    """A photo of this game: post (within 12h of tip-off) and image both after tip-off; posts naming both
    teams first, then posts naming one of them."""
    until = tipoff + dt.timedelta(hours=12)
    return (_find(team_names, tipoff, media_since=tipoff, until=until, need_all=True)
            or _find(team_names, tipoff, media_since=tipoff, until=until))


def news_photo(names, days=10):
    """A recent editor photo of the person / team the news is about."""
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    return _find(names, since)
