#!/usr/bin/env python3
"""Totalbasket NBA bot.

Every run:
  1. finds finished NBA games (ESPN) that we have not covered -> one article per game
  2. finds new ESPN NBA news stories (quotes from coaches/players, injuries, moves)
  3. has Claude write an original Greek article for each, draws a featured image,
     and publishes it to totalbasket.gr (category NBA).

Env:
  WP_URL (default https://totalbasket.gr), WP_USER, WP_APP_PASSWORD
  WP_STATUS   publish | draft          (default publish)
  DRY_RUN=1   write articles to ./out instead of WordPress
  CLAUDE_MODEL (default opus), MAX_POSTS (default 10)
  CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY  (used by the claude CLI)
"""
import base64
import datetime as dt
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import card
import photos
from teams import TEAMS, colours, greek, logo_url

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state.json")
ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
ESPN_CONTENT = "https://content.core.api.espn.com/v1/sports/news/"

WP_URL = os.environ.get("WP_URL", "https://totalbasket.gr").rstrip("/")
WP_STATUS = os.environ.get("WP_STATUS", "publish")
DRY_RUN = os.environ.get("DRY_RUN") == "1"
MODEL = os.environ.get("CLAUDE_MODEL", "opus")
MAX_POSTS = int(os.environ.get("MAX_POSTS", "10"))
NBA_CATEGORY = 6
NBA_TAG = 153
TAG_2ND_TOP = 113               # the run's most important article
TAG_3RD_TOP = 114               # every other article
SKIP_SEASON_TYPES = {1, 4}       # preseason, all-star
RECAP_WAIT_HOURS = 3             # wait this long for the AP recap (it carries the quotes)
NEWS_MAX_AGE_HOURS = 30
MAX_ATTEMPTS = 3

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def now():
    return dt.datetime.now(dt.timezone.utc)


def get_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def strip_html(s):
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s or "", flags=re.S)
    s = re.sub(r"<br\s*/?>|</p>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", s)).strip()


# ---------------------------------------------------------------- state

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"games": {}, "news": {}}


def save_state(state):
    cutoff = (now() - dt.timedelta(days=14)).isoformat()
    for k in ("games", "news"):
        state[k] = {i: v for i, v in state[k].items() if v.get("seen", "") >= cutoff}
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)


# ---------------------------------------------------------------- ESPN: games

def finished_games():
    today = now()
    seen = set()
    for delta in (2, 1, 0):
        day = (today - dt.timedelta(days=delta)).strftime("%Y%m%d")
        for e in get_json(f"{ESPN}/scoreboard?dates={day}").get("events", []):
            if e["id"] in seen:
                continue
            seen.add(e["id"])
            comp = e["competitions"][0]
            if comp["status"]["type"].get("completed") and e["season"].get("type") not in SKIP_SEASON_TYPES:
                yield e


def player_lines(box_team):
    stats = box_team["statistics"][0]
    names = stats["names"]
    out = []
    for a in stats["athletes"]:
        if a.get("didNotPlay") or not a.get("stats"):
            continue
        s = dict(zip(names, a["stats"]))
        out.append({"player": a["athlete"]["displayName"], "starter": a.get("starter", False), **s})
    out.sort(key=lambda p: int(p.get("PTS") or 0), reverse=True)
    return out


def game_facts(event):
    s = get_json(f"{ESPN}/summary?event={event['id']}")
    comp = s["header"]["competitions"][0]
    teams = []
    for c in comp["competitors"]:
        t = c["team"]
        teams.append({
            "espn_id": t["id"],
            "name_en": t["displayName"],
            "name_gr": greek(t["id"], t["displayName"]),
            "city_gr": TEAMS.get(t["id"], ("", t["location"]))[1],
            "home_away": c["homeAway"],
            "score": c.get("score"),
            "winner": c.get("winner", False),
            "record": (c.get("record") or [{}])[0].get("summary") if c.get("record") else None,
            "quarters": [l.get("displayValue") for l in c.get("linescores", [])],
        })
    box = {}
    for bt in s.get("boxscore", {}).get("players", []):
        name = greek(bt["team"]["id"], bt["team"]["displayName"])
        box[name] = player_lines(bt)[:8]
    team_stats = {}
    for bt in s.get("boxscore", {}).get("teams", []):
        team_stats[greek(bt["team"]["id"], bt["team"]["displayName"])] = {
            st["label"]: st["displayValue"] for st in bt.get("statistics", [])}
    art = s.get("article") or {}
    return {
        "type": "game",
        "competition": "NBA " + ({2: "κανονική περίοδος", 3: "playoffs", 5: "play-in"}
                                 .get(event["season"].get("type"), "")),
        "date_utc": event["date"],
        "venue": (s.get("gameInfo", {}).get("venue") or {}).get("fullName"),
        "status": comp["status"]["type"].get("detail"),
        "teams": teams,
        "box_score_top_players": box,
        "team_stats": team_stats,
        "recap_en": strip_html(art.get("story", ""))[:9000],
    }, bool(art.get("story"))


# ---------------------------------------------------------------- ESPN: news

def news_items():
    for a in get_json(f"{ESPN}/news?limit=40").get("articles", []):
        if a.get("type") not in ("HeadlineNews", "Story"):
            continue
        href = a.get("links", {}).get("api", {}).get("self", {}).get("href", "")
        if "/sports/news/" not in href:
            continue
        yield a


def news_facts(item):
    full = get_json(ESPN_CONTENT + str(item["id"]))["headlines"][0]
    team_ids = [str(c.get("teamId") or c.get("team", {}).get("id"))
                for c in full.get("categories", []) if c.get("type") == "team"]
    return {
        "type": "news",
        "published_utc": item.get("published"),
        "headline_en": full.get("headline"),
        "description_en": full.get("description"),
        "teams_gr": [greek(t) for t in team_ids if greek(t)],
        "story_en": strip_html(full.get("story", ""))[:9000],
    }, (team_ids[0] if team_ids else None)


# ---------------------------------------------------------------- Claude

STYLE = """Είσαι συντάκτης του ελληνικού site μπάσκετ totalbasket.gr και γράφεις για το NBA.

Κανόνες:
- Γράφεις ΠΡΩΤΟΤΥΠΟ άρθρο στα ελληνικά, με δικά σου λόγια. ΔΕΝ μεταφράζεις παράγραφο-παράγραφο το αγγλικό κείμενο.
- Χρησιμοποιείς ΜΟΝΟ γεγονότα, αριθμούς και δηλώσεις που υπάρχουν στα δεδομένα. Δεν εφευρίσκεις τίποτα.
- Για στατιστικά παικτών, αν διαφέρει το recap από το box score, ισχύει το box score.
- Δηλώσεις προπονητών/παικτών: μεταφράζεις στα ελληνικά, μέσα σε «εισαγωγικά», με σαφή απόδοση (ποιος το είπε). Διάλεξε τις 1-3 πιο ενδιαφέρουσες.
- Ονόματα παικτών/προπονητών με ελληνικούς χαρακτήρες όπως τα γράφουν τα ελληνικά αθλητικά site (π.χ. Λεμπρόν Τζέιμς, Νίκολα Γιόκιτς, Βίκτορ Ουεμπανιάμα, Σάι Γκίλτζιους-Αλεξάντερ, Γιάννης Αντετοκούνμπο).
- Ομάδες με το ελληνικό τους παρατσούκλι όπως δίνεται στα δεδομένα (Λέικερς, Σέλτικς, Νικς ...). Εναλλακτικά για ποικιλία η πόλη (π.χ. «το Λος Άντζελες», «η Βοστώνη»).
- Αν συμμετέχει Έλληνας παίκτης (Αντετοκούνμπο, Πουλακίδας κ.ά.), τον αναδεικνύεις.
- Ύφος ζωντανό, δημοσιογραφικό, όπως ένα ελληνικό αθλητικό site. Όροι όπως triple-double, double-double, rookie, clutch μένουν στα αγγλικά.
- Μορφή κειμένου: 4-6 παράγραφοι, HTML μόνο με <p class="wp-block-paragraph">, <strong> (λίγο, για τα πιο σημαντικά) και <blockquote class="wp-block-quote"><p>...</p></blockquote> αν θες για μία δήλωση. Τίποτα άλλο.
- Τίτλος: σύντομος, πιασάρικος, ελληνικός, χωρίς τελεία, χωρίς emoji. Μπορεί να έχει δήλωση σε «» ή θαυμαστικό όπως ο ελληνικός αθλητικός τύπος.
- Μην αναφέρεις ESPN, AP ή άλλο μέσο ως πηγή του κειμένου.
- Τίτλος και κείμενο ΜΟΝΟ με ελληνικούς χαρακτήρες στα ονόματα (ποτέ μισό ελληνικά μισό λατινικά, π.χ. «Εμπίντ» και όχι «Εμπίid»).
- photo_person: αν το άρθρο αφορά κυρίως ΕΝΑ πρόσωπο (παίκτη/προπονητή/στέλεχος), το πλήρες όνομά του στα ΑΓΓΛΙΚΑ (π.χ. "Joel Embiid"), αλλιώς "".
- importance: 1-10 πόσο σημαντικό/ελκυστικό είναι για Έλληνα αναγνώστη (σούπερ σταρ, Έλληνες παίκτες, μεγάλες μεταγραφές = υψηλά).

Απαντάς ΜΟΝΟ με ένα JSON αντικείμενο, χωρίς τίποτα άλλο:
{"publish": true, "reason": "", "title": "...", "excerpt": "1-2 προτάσεις", "content": "<p ...>...</p>", "people": ["ελληνικά ονόματα των 1-4 κύριων προσώπων"], "photo_person": "", "importance": 5}"""

GAME_TASK = """Γράψε το άρθρο για τον αγώνα. Στην αρχή το τελικό σκορ και ο πρωταγωνιστής, μετά η εξέλιξη (περίοδοι, κρίσιμο σημείο),
οι κορυφαίοι και των δύο ομάδων με αριθμούς, οι δηλώσεις αν υπάρχουν στο recap, και τα ρεκόρ των ομάδων. Το publish είναι πάντα true."""

NEWS_TASK = """Αξιολόγησε την είδηση. Βάλε publish=false (με reason) αν είναι: fantasy/στοιχηματική ανάλυση, λίστα/ranking/γνώμη αρθρογράφου,
βίντεο χωρίς ουσία, θέμα εκτός NBA, ή κάτι πολύ ασήμαντο. Βάλε publish=true για δηλώσεις προπονητών/παικτών, τραυματισμούς, μεταγραφές/ανταλλαγές/
συμβόλαια/απολύσεις, ρεκόρ, τιμωρίες και κάθε είδηση με ειδησεογραφική αξία, και γράψε το άρθρο.
ΣΗΜΑΝΤΙΚΟ: στο "already_on_site" είναι οι τίτλοι που υπάρχουν ήδη στο site. Βάλε publish=false αν η είδηση καλύπτει το ίδιο θέμα
(π.χ. είναι ουσιαστικά αποτέλεσμα/περίληψη αγώνα που ήδη υπάρχει, ή roundup αποτελεσμάτων). Δηλώσεις μετά από αγώνα με δικό τους θέμα επιτρέπονται."""


class AuthError(Exception):
    """Claude or WordPress rejected our key: stop the run, don't burn the item's attempts."""


MIXED_SCRIPT = re.compile(r"[\u0370-\u03ff\u1f00-\u1fff][A-Za-z]|[A-Za-z][\u0370-\u03ff\u1f00-\u1fff]")


def ask_claude(task, facts):
    for attempt in range(2):
        art = _ask_claude(task, facts, retry_note=attempt > 0)
        if not art.get("publish") or not MIXED_SCRIPT.search(art["title"] + art.get("excerpt", "")):
            return art
        log("  mixed Greek/Latin title, rewriting:", art["title"])
    raise RuntimeError("title keeps mixing Greek and Latin letters")


def _ask_claude(task, facts, retry_note=False):
    prompt = f"{STYLE}\n\n{task}\n\nΔΕΔΟΜΕΝΑ (JSON):\n{json.dumps(facts, ensure_ascii=False, indent=1)}"
    if retry_note:
        prompt += "\n\nΠΡΟΣΟΧΗ: η προηγούμενη απάντηση είχε λέξη με ανακατεμένα ελληνικά και λατινικά γράμματα. Γράψ' τα όλα σωστά."
    r = subprocess.run(
        ["claude", "-p", "--output-format", "json", "--model", MODEL, "--max-turns", "2"],
        input=prompt, capture_output=True, text=True, timeout=600)
    if '"api_error_status":401' in r.stdout or "Failed to authenticate" in r.stdout:
        raise AuthError("Claude token rejected - set CLAUDE_CODE_OAUTH_TOKEN again")
    if r.returncode != 0:
        raise RuntimeError(f"claude failed: {r.stderr[-800:] or r.stdout[-800:]}")
    text = json.loads(r.stdout).get("result", "")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError(f"no JSON in answer: {text[:300]}")
    art = json.loads(m.group(0))
    if art.get("publish") and not (art.get("title") and art.get("content")):
        raise RuntimeError("answer missing title/content")
    return art


# ---------------------------------------------------------------- WordPress

def wp(method, path, data=None, headers=None, raw=None):
    auth = base64.b64encode(f'{os.environ["WP_USER"]}:{os.environ["WP_APP_PASSWORD"]}'.encode()).decode()
    h = {"Authorization": "Basic " + auth, **UA, **(headers or {})}
    body = raw
    if data is not None:
        body = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{WP_URL}/wp-json/wp/v2/{path}", data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise AuthError(f"WordPress rejected WP_USER / WP_APP_PASSWORD ({method} {path})")
        raise RuntimeError(f"WordPress {method} {path}: {e.code} {e.read()[:400]!r}")


_tag_cache = {}


def find_tags(names):
    """Only reuse tags that already exist on the site (exact name match) — never create new ones."""
    ids = [NBA_TAG]
    for n in names:
        n = (n or "").strip()
        if not n:
            continue
        if n not in _tag_cache:
            res = get_json(f"{WP_URL}/wp-json/wp/v2/tags?search={urllib.parse.quote(n)}&_fields=id,name")
            _tag_cache[n] = next((t["id"] for t in res if html.unescape(t["name"]).lower() == n.lower()), None)
        if _tag_cache[n] and _tag_cache[n] not in ids:
            ids.append(_tag_cache[n])
    return ids


def publish(art, image_path, slug_hint):
    if DRY_RUN:
        os.makedirs(os.path.join(HERE, "out"), exist_ok=True)
        base = os.path.join(HERE, "out", slug_hint)
        with open(base + ".html", "w") as f:
            f.write(f"<h1>{html.escape(art['title'])}</h1><p><img src='{os.path.basename(image_path)}' width=600></p>"
                    f"<p><em>{html.escape(art.get('excerpt', ''))}</em></p>{art['content']}"
                    f"<p>tags: {html.escape(', '.join(art.get('people', [])))}</p>")
        os.replace(image_path, base + ".jpg")
        return {"id": "dry-run", "link": base + ".html"}
    with open(image_path, "rb") as f:
        media = wp("POST", "media", raw=f.read(), headers={
            "Content-Type": "image/jpeg",
            "Content-Disposition": f'attachment; filename="nba-{slug_hint}.jpg"'})
    wp("POST", f"media/{media['id']}", {"alt_text": art["title"], "caption": art.get("_caption", "")})
    tags = find_tags(art.get("people", []) + art.get("_teams", [])) + [TAG_3RD_TOP]
    return wp("POST", "posts", {
        "title": art["title"], "content": art["content"], "excerpt": art.get("excerpt", ""),
        "status": WP_STATUS, "categories": [NBA_CATEGORY], "tags": tags,
        "featured_media": media["id"]})


# ---------------------------------------------------------------- main

def person_image(art):
    """Free-licence photo of the article's main person; adds the required credit line to the text."""
    name = (art.get("photo_person") or "").strip()
    if not name:
        return None
    try:
        found = photos.find(name)
    except Exception as ex:
        log("  photo lookup failed:", name, ex)
        return None
    if not found:
        log("  no free photo for", name)
        return None
    img, credit = found
    credit = re.sub(r"\s+", " ", credit)[:200]
    art["content"] += f'\n\n<p class="wp-block-paragraph"><em>Φωτογραφία: {html.escape(credit)}</em></p>'
    art["_caption"] = credit
    return card.photo_card(tempfile.mktemp(suffix=".jpg"), img)


def recent_site_titles():
    try:
        res = get_json(f"{WP_URL}/wp-json/wp/v2/posts?categories={NBA_CATEGORY}&per_page=40&_fields=title")
        return [html.unescape(p["title"]["rendered"]) for p in res]
    except Exception as ex:
        log("could not read site titles:", ex)
        return []


def promote_top(published):
    """The most important article of this run goes to 2ndTOP, the rest stay 3rdTOP."""
    if DRY_RUN or not published:
        return
    best = max(published, key=lambda p: p["importance"])
    tags = [t for t in best["tags"] if t != TAG_3RD_TOP] + [TAG_2ND_TOP]
    wp("POST", f"posts/{best['id']}", {"tags": tags})
    log("2ndTOP:", best["title"])


def run():
    state = load_state()
    try:
        _run(state)
    finally:
        save_state(state)       # also on errors, so published items are never posted twice


def _run(state):
    posted = 0
    published = []
    stamp = now().isoformat()
    site_titles = recent_site_titles()

    def record(res, art):
        published.append({"id": res["id"], "tags": res.get("tags", []), "title": art["title"],
                          "importance": int(art.get("importance") or 0)})
        site_titles.insert(0, art["title"])

    def budget():
        return posted < MAX_POSTS

    # 1. games
    for e in finished_games():
        g = state["games"].setdefault(e["id"], {"seen": stamp, "attempts": 0})
        if g.get("done") or g["attempts"] >= MAX_ATTEMPTS or not budget():
            continue
        try:
            facts, has_recap = game_facts(e)
            first = dt.datetime.fromisoformat(g["seen"])
            if not has_recap and now() - first < dt.timedelta(hours=RECAP_WAIT_HOURS):
                log("waiting for recap:", e["name"])
                continue
            g["attempts"] += 1
            log("writing game:", e["name"])
            art = ask_claude(GAME_TASK, facts)
            away = next(t for t in facts["teams"] if t["home_away"] == "away")
            home = next(t for t in facts["teams"] if t["home_away"] == "home")
            art["_teams"] = [away["name_gr"], home["name_gr"]]
            img = card.game_card(tempfile.mktemp(suffix=".jpg"),
                                 {"name": away["name_gr"], "score": away["score"], "winner": away["winner"],
                                  "colour": colours(away["espn_id"])[0], "logo": logo_url(away["espn_id"])},
                                 {"name": home["name_gr"], "score": home["score"], "winner": home["winner"],
                                  "colour": colours(home["espn_id"])[0], "logo": logo_url(home["espn_id"])})
            res = publish(art, img, f"game-{e['id']}")
            g.update(done=True, post=res["id"])
            record(res, art)
            posted += 1
            log("  ->", res.get("link"))
        except AuthError:
            g["attempts"] -= 1
            raise
        except Exception as ex:  # keep going with the other items
            log("  ERROR", e["name"], ex)

    # 2. news
    for item in news_items():
        nid = str(item["id"])
        if nid in state["news"]:
            n = state["news"][nid]
        else:
            n = state["news"][nid] = {"seen": stamp, "attempts": 0}
            pub = dt.datetime.fromisoformat(item["published"].replace("Z", "+00:00"))
            if now() - pub > dt.timedelta(hours=NEWS_MAX_AGE_HOURS):
                n["done"] = "too-old"
        if n.get("done") or n["attempts"] >= MAX_ATTEMPTS or not budget():
            continue
        try:
            n["attempts"] += 1
            log("judging news:", item["headline"])
            facts, team_id = news_facts(item)
            facts["already_on_site"] = site_titles[:40]
            art = ask_claude(NEWS_TASK, facts)
            if not art.get("publish"):
                n["done"] = "skipped: " + art.get("reason", "")[:120]
                log("  skipped:", art.get("reason"))
                continue
            art["_teams"] = facts["teams_gr"]
            img = person_image(art) or card.news_card(
                tempfile.mktemp(suffix=".jpg"), art["title"],
                colours(team_id)[0] if team_id else "1d1d1d", logo_url(team_id))
            res = publish(art, img, f"news-{nid}")
            n.update(done=True, post=res["id"])
            record(res, art)
            posted += 1
            log("  ->", res.get("link"))
        except AuthError:
            n["attempts"] -= 1
            raise
        except Exception as ex:  # keep going with the other items
            log("  ERROR", item["headline"], ex)

    promote_top(published)
    log(f"done, {posted} article(s)")


if __name__ == "__main__":
    if not DRY_RUN and not (os.environ.get("WP_USER") and os.environ.get("WP_APP_PASSWORD")):
        sys.exit("WP_USER / WP_APP_PASSWORD missing")
    try:
        run()
    except AuthError as ex:
        sys.exit(f"STOPPED: {ex}")
